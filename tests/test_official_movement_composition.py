from __future__ import annotations

import xml.etree.ElementTree as ET

import pytest

from torii_sumo.core.official_movement_composition import compose_official_movements


@pytest.mark.parametrize("role", ["ingress", "egress"])
@pytest.mark.parametrize("fault", [None, "other_owner", "reverse", "permission", "gap", "disconnected"])
def test_original_map_boundary_can_lie_on_its_native_internal_lane(role, fault):
    from torii_sumo.core.official_movement_composition import _original_lane_bindings
    from torii_sumo.core.source_movement_support import _index

    root = ET.Element("net")
    for node in ("west", "junction", "east", "other"):
        ET.SubElement(root, "junction", id=node)
    for edge_id, start, end, shape in (("in", "west", "junction", "-20,0 0,0"), ("out", "junction", "east", "24,0 44,0")):
        edge = ET.SubElement(root, "edge", id=edge_id, **{"from": start, "to": end})
        ET.SubElement(edge, "lane", id=f"{edge_id}_0", index="0", shape=shape)
    owner = "other" if fault == "other_owner" else "junction"
    edge_id = f":{owner}_0"
    internal = ET.SubElement(root, "edge", id=edge_id, function="internal")
    lane = ET.SubElement(internal, "lane", id=f"{edge_id}_0", index="0", shape="0,0 24,0")
    if fault == "reverse":
        lane.set("shape", "24,0 0,0")
    elif fault == "permission":
        lane.set("allow", "delivery")
    elif fault == "gap":
        lane.set("shape", "3,0 21,0")
    ET.SubElement(root, "connection", **{"from": "in", "to": "out", "fromLane": "0", "toLane": "0", "via": f"{edge_id}_0"})
    if fault != "disconnected":
        ET.SubElement(root, "connection", **{"from": edge_id, "to": "out", "fromLane": "0", "toLane": "0"})
    point = (13, 0) if role == "ingress" else (11, 0)
    official = {"lane_type": "vehicle", "direction_role": role,
                "shape_network": [point, (-20, 0) if role == "ingress" else (44, 0)],
                "junction_endpoint_network": point, "allowed_vehicle_classes": ["passenger"]}
    bindings, reviews = _original_lane_bindings(_index(root), {"official": official}, {"junction"}, 10, .5)
    expected = {"official": "in_0" if role == "ingress" else "out_0"} if fault is None else {}
    assert bindings == expected
    if fault is None:
        assert reviews[0]["reason"] == "official_boundary_on_source_internal_lane"
        assert reviews[0]["source_internal_lane_id"] == f"{edge_id}_0"


@pytest.mark.parametrize("role,point", [("ingress", (5, 0)), ("egress", (25, 0))])
def test_official_stop_section_may_cut_inside_an_existing_normal_lane(role, point):
    from torii_sumo.core.official_movement_composition import _original_lane_bindings
    from torii_sumo.core.source_movement_support import _index

    root = ET.Element("net")
    edge = ET.SubElement(root, "edge", id="road", **{"from": "junction" if role == "egress" else "outside", "to": "junction" if role == "ingress" else "outside"})
    ET.SubElement(edge, "lane", id="road_0", index="0", shape="0,0 30,0")
    official = {"direction_role": role, "shape_network": [point, (-20, 0) if role == "ingress" else (50, 0)], "junction_endpoint_network": point}
    bindings, reviews = _original_lane_bindings(_index(root), {"official": official}, {"junction"}, 10, .5)
    assert bindings == {"official": "road_0"}
    assert reviews[0]["reason"] == "official_boundary_on_source_lane"


def test_native_anchor_candidates_use_the_same_distance_definition():
    from torii_sumo.core.official_movement_composition import _original_lane_bindings
    from torii_sumo.core.source_movement_support import _index

    root = ET.Element("net")
    for node in ("west", "junction", "east"):
        ET.SubElement(root, "junction", id=node)
    for edge_id, start, end in (("in", "west", "junction"), ("out", "junction", "east")):
        edge = ET.SubElement(root, "edge", id=edge_id, **{"from": start, "to": end})
        for i, y in enumerate((0, 3.2)):
            shape = f"-20,{y} {2 * i},{y}" if edge_id == "in" else f"24,{y} 44,{y}"
            ET.SubElement(edge, "lane", id=f"{edge_id}_{i}", index=str(i), shape=shape)
    edge = ET.SubElement(root, "edge", id=":junction_0", function="internal")
    for i, y in enumerate((0, 3.2)):
        ET.SubElement(edge, "lane", id=f":junction_0_{i}", index=str(i), shape=f"{2 * i},{y} 24,{y}")
        ET.SubElement(root, "connection", **{"from": "in", "to": "out", "fromLane": str(i), "toLane": str(i), "via": f":junction_0_{i}"})
        ET.SubElement(root, "connection", **{"from": ":junction_0", "to": "out", "fromLane": str(i), "toLane": str(i)})
    lane = {"direction_role": "ingress", "shape_network": [(12, 3.2), (-20, 3.2)], "junction_endpoint_network": (12, 3.2)}
    bindings, _ = _original_lane_bindings(_index(root), {"official": lane}, {"junction"}, 10, .5)
    assert bindings == {"official": "in_1"}


def _serial_candidate_fixture():
    return ET.fromstring('''<net><junction id="left"/><junction id="cut"/><junction id="right"/>
      <edge id="road#0" from="left" to="cut"><lane id="road#0_0" index="0" shape="-10,0 0,0"/></edge>
      <edge id="road#1" from="cut" to="right"><lane id="road#1_0" index="0" shape="0.2,0 10,0"/></edge>
      <edge id=":cut_0" function="internal"><lane id=":cut_0_0" index="0" shape="0,0 0.2,0"/></edge>
      <connection from="road#0" to="road#1" fromLane="0" toLane="0" dir="s" via=":cut_0_0"/>
      <connection from=":cut_0" to="road#1" fromLane="0" toLane="0" dir="s"/>
    </net>''')


def _overlapping_serial_fixture():
    root = _serial_candidate_fixture()
    root.find("edge[@id='road#0']/lane").set("shape", "-10,0 4,0")
    root.find("edge[@id='road#1']/lane").set("shape", "0,0 0.2,0")
    root.find("edge[@id=':cut_0']/lane").set("shape", "4,0 0,0")
    for edge_id in ("road#0", "road#1"):
        ET.SubElement(root.find(f"edge[@id='{edge_id}']/lane"), "param", key="origId", value="same-way")
    return root


@pytest.mark.parametrize("role", ["ingress", "egress"])
def test_source_overlap_can_prove_lane_identity_but_not_native_curve_quality(role):
    from torii_sumo.core.official_movement_composition import _original_lane_bindings
    from torii_sumo.core.source_movement_support import _index

    root = _overlapping_serial_fixture()
    point, away, members, expected = ((0.3, 0), (-10, 0), {"cut", "right"}, "road#0_0") if role == "ingress" else ((0.1, 0), (10, 0), {"left", "cut"}, "road#1_0")
    lane = {"direction_role": role, "junction_endpoint_network": point, "shape_network": [point, away]}
    bindings, reviews = _original_lane_bindings(_index(root), {"official": lane}, members, 10, 0.5)
    assert bindings == {"official": expected}
    proof = next(row for row in reviews if row.get("reason") == "same_mode_one_to_one_serial_lane_cut")
    defect = proof["source_geometry_defects"][0]
    assert defect["reason"] == "overlapping_source_cut_with_reverse_internal_geometry"
    assert defect["orig_id"] == "same-way"
    assert defect["requires_reconstructed_geometry"] is True


def test_context_retains_the_serial_identity_geometry_defect_record():
    from torii_sumo.core.official_movement_composition import _original_lane_bindings, _resolve_original_context
    from torii_sumo.core.source_movement_support import _index

    root = _overlapping_serial_fixture()
    for name, start, end, shape in (("lookalike", "left", "cut", "-10,0.05 4,0.05"), ("out", "right", "outside", "10,0 20,0")):
        edge = ET.SubElement(root, "edge", id=name, **{"from": start, "to": end})
        ET.SubElement(edge, "lane", id=f"{name}_0", shape=shape)
    ET.SubElement(root, "connection", **{"from": "road#1", "to": "out", "fromLane": "0", "toLane": "0"})
    lanes = {"in": {"direction_role": "ingress", "junction_endpoint_network": (0.3, 0), "shape_network": [(0.3, 0), (-10, 0)]},
             "out": {"direction_role": "egress", "junction_endpoint_network": (10, 0), "shape_network": [(10, 0), (20, 0)]}}
    index = _index(root)
    bindings, reviews = _original_lane_bindings(index, lanes, {"cut", "right"}, 10, 0.5)
    assert "in" not in bindings
    _resolve_original_context(index, lanes, [{"ingress_lane_id": "in", "egress_lane_id": "out"}], bindings, reviews, {"cut", "right"}, 0.5)
    assert bindings["in"] == "road#0_0"
    review = next(row for row in reviews if row["lane_id"] == "in")
    assert review["reason"] == "unique_original_movement_context"
    assert review["source_geometry_defects"][0]["requires_reconstructed_geometry"] is True


@pytest.mark.parametrize("has_lineage", [True, False])
def test_source_extension_must_not_copy_a_reverse_internal_cut(has_lineage):
    from torii_sumo.core.official_movement_composition import _source_boundary_extensions
    from torii_sumo.core.source_movement_support import _index

    source = _overlapping_serial_fixture()
    if not has_lineage:
        for edge_id in ("road#0", "road#1"):
            lane = source.find(f"edge[@id='{edge_id}']/lane")
            lane.remove(lane.find("param"))
    candidate = ET.fromstring('<net><edge id="road#0" from="left" to="joined"><lane id="road#0_0" shape="-10,0 -2,0"/></edge></net>')
    lanes = {"official": {"direction_role": "ingress", "junction_endpoint_network": (0.3, 0), "shape_network": [(-5, 0), (0.3, 0)]}}
    extensions, reviews = _source_boundary_extensions(
        _index(source), _index(candidate), lanes=lanes, source_lanes=lanes,
        source_bindings={"official": "road#1_0"}, members={"cut", "right"},
        bindings={"ingress": {}, "egress": {}}, boundary_candidates={"ingress": {("road#0", 0): [(-10, 0), (-2, 0)]}, "egress": {}},
        links=[], aliases={}, transform=lambda point: point, geometry_limit=3, anchor_limit=10,
    )
    assert not extensions
    assert any(row["reason"] == "source_internal_geometry_reverses_a_straight_continuation" for row in reviews)


@pytest.mark.parametrize("fault", ["missing_lineage", "other_lineage", "lateral_offset", "outside_interval", "curved_loop", "reverse_external"])
def test_backward_cut_identity_requires_independent_same_lane_overlap_evidence(fault):
    from torii_sumo.core.official_movement_composition import _collapse_serial_lane_candidates
    from torii_sumo.core.source_movement_support import _index

    root = _overlapping_serial_fixture()
    target = root.find("edge[@id='road#1']/lane")
    internal = root.find("edge[@id=':cut_0']/lane")
    if fault == "missing_lineage":
        target.remove(target.find("param"))
    elif fault == "other_lineage":
        target.find("param").set("value", "another-way")
    elif fault == "lateral_offset":
        target.set("shape", "0,1 0.2,1")
        internal.set("shape", "4,0 0,1")
    elif fault == "outside_interval":
        target.set("shape", "-11,0 -10.8,0")
        internal.set("shape", "4,0 -11,0")
    elif fault == "curved_loop":
        internal.set("shape", "4,0 5,5 0,0")
    elif fault == "reverse_external":
        target.set("shape", "0,0 -0.2,0")
    choices = [(0.1, "road#0_0"), (0.2, "road#1_0")]
    collapsed, proofs = _collapse_serial_lane_candidates(_index(root), choices, {"cut", "right"}, "ingress")
    assert collapsed == choices
    assert not proofs


@pytest.mark.parametrize("role", ["ingress", "egress"])
def test_serial_lane_candidates_resolve_to_the_actual_external_cut(role):
    from torii_sumo.core.official_movement_composition import _original_lane_bindings
    from torii_sumo.core.source_movement_support import _index

    root = _serial_candidate_fixture()
    point, away, members, expected = ((0.3, 0), (-10, 0), {"cut", "right"}, "road#0_0") if role == "ingress" else ((-0.1, 0), (10, 0), {"left", "cut"}, "road#1_0")
    lane = {"direction_role": role, "junction_endpoint_network": point, "shape_network": [point, away]}
    bindings, reviews = _original_lane_bindings(_index(root), {"official": lane}, members, 10, 0.5)
    assert bindings == {"official": expected}
    proof = next(row for row in reviews if row.get("reason") == "same_mode_one_to_one_serial_lane_cut")
    assert proof["equivalent_segment_lane_ids"] == ["road#0_0", "road#1_0"]
    assert proof["serial_source_lane_paths"] == [["road#0_0", ":cut_0_0", "road#1_0"]]


@pytest.mark.parametrize("fault", ["turn", "fork", "merge", "permission", "lane_change", "direction", "gap", "wrong_owner", "outside_scope", "no_internal_chain"])
def test_serial_identity_requires_one_to_one_straight_same_mode_internal_chain(fault):
    from torii_sumo.core.official_movement_composition import _collapse_serial_lane_candidates
    from torii_sumo.core.source_movement_support import _index

    root = _serial_candidate_fixture()
    connection = root.find("connection[@from='road#0']")
    continuation = root.find("connection[@from=':cut_0']")
    internal = root.find("edge[@id=':cut_0']/lane")
    members, target = {"cut", "right"}, "road#1_0"
    if fault == "turn":
        connection.set("dir", "r")
    elif fault in {"fork", "merge"}:
        edge = ET.SubElement(root, "edge", id="other", **{"from": "cut" if fault == "fork" else "left", "to": "right" if fault == "fork" else "cut"})
        ET.SubElement(edge, "lane", id="other_0", shape="0.2,3 10,3")
        ET.SubElement(root, "connection", **{"from": "road#0" if fault == "fork" else "other", "to": "other" if fault == "fork" else "road#1", "fromLane": "0", "toLane": "0", "dir": "s"})
    elif fault == "permission":
        internal.set("allow", "bus")
    elif fault == "lane_change":
        for name in ("road#0", "road#1"):
            ET.SubElement(root.find(f"edge[@id='{name}']"), "lane", id=f"{name}_1", index="1", shape="0.2,0 10,0")
        connection.set("toLane", "1")
        continuation.set("toLane", "1")
        target = "road#1_1"
    elif fault == "direction":
        internal.set("shape", "0,0 0.1,3 0.2,0")
    elif fault == "gap":
        internal.set("shape", "0.15,0 0.2,0")
    elif fault == "wrong_owner":
        ET.SubElement(root, "junction", id="wrong")
        root.find("edge[@id=':cut_0']").set("id", ":wrong_0")
        internal.set("id", ":wrong_0_0")
        connection.set("via", ":wrong_0_0")
        continuation.set("from", ":wrong_0")
    elif fault == "outside_scope":
        members = {"right"}
    elif fault == "no_internal_chain":
        connection.attrib.pop("via")
        root.remove(continuation)
    candidates = [(0.1, "road#0_0"), (0.2, target)]
    collapsed, proofs = _collapse_serial_lane_candidates(_index(root), candidates, members, "ingress")
    assert collapsed == candidates
    assert not proofs


def test_context_cannot_replace_nearby_ambiguous_lanes_with_a_distant_connected_lane():
    from torii_sumo.core.official_movement_composition import _original_lane_bindings, _resolve_original_context
    from torii_sumo.core.source_movement_support import _index

    root = ET.Element("net")
    for edge_id, y in (("near-left", -0.1), ("near-right", 0.1), ("distant", 3.5)):
        edge = ET.SubElement(root, "edge", id=edge_id, **{"from": "outside", "to": "junction"})
        ET.SubElement(edge, "lane", id=f"{edge_id}_0", shape=f"-10,{y} 0,{y}")
    edge = ET.SubElement(root, "edge", id="out", **{"from": "junction", "to": "outside"})
    ET.SubElement(edge, "lane", id="out_0", shape="10,0 20,0")
    ET.SubElement(root, "connection", **{"from": "distant", "to": "out", "fromLane": "0", "toLane": "0"})
    lanes = {"in": {"direction_role": "ingress", "junction_endpoint_network": (0, 0), "shape_network": [(0, 0), (-10, 0)]},
             "out": {"direction_role": "egress", "junction_endpoint_network": (10, 0), "shape_network": [(10, 0), (20, 0)]}}
    index = _index(root)
    bindings, reviews = _original_lane_bindings(index, lanes, {"junction"}, 10, 0.5)
    assert "in" not in bindings
    _resolve_original_context(index, lanes, [{"ingress_lane_id": "in", "egress_lane_id": "out"}], bindings, reviews, {"junction"}, 0.5)
    assert "in" not in bindings


def _fixtures():
    original = ET.Element("net")
    candidate = ET.Element("net")
    for root in (original, candidate):
        for node in ("outside-in", "left", "middle", "right", "outside-out", "joined"):
            ET.SubElement(root, "junction", id=node, x="0", y="0", type="priority")
    for root in (original, candidate):
        for edge_id, start, end, xs in (("in", "outside-in", "left" if root is original else "joined", (-20, -10)), ("out", "right" if root is original else "joined", "outside-out", (10, 20))):
            edge = ET.SubElement(root, "edge", id=edge_id, **{"from": start, "to": end})
            for index, y in enumerate((-2.0, 2.0)):
                ET.SubElement(edge, "lane", id=f"{edge_id}_{index}", index=str(index), shape=f"{xs[0]},{y} {xs[1]},{y}", speed="10", length="10")
    for edge_id, start, end, xs in (("egress", "left", "middle", (-5, 0)), ("ingress", "middle", "right", (0, 5))):
        edge = ET.SubElement(original, "edge", id=edge_id, **{"from": start, "to": end})
        for index, y in enumerate((-2.0, 2.0)):
            ET.SubElement(edge, "lane", id=f"{edge_id}_{index}", index=str(index), shape=f"{xs[0]},{y} {xs[1]},{y}", speed="10", length="5")
    internal = ET.SubElement(candidate, "edge", id=":joined_0", function="internal")
    for index, y in enumerate((-2.0, 2.0)):
        ET.SubElement(internal, "lane", id=f":joined_0_{index}", index=str(index), shape=f"-10,{y} 10,{y}", speed="10", length="20")
        for source, target in (("in", "egress"), ("egress", "ingress"), ("ingress", "out")):
            ET.SubElement(original, "connection", **{"from": source, "to": target, "fromLane": str(index), "toLane": str(index), "tl": "shared-controller", "linkIndex": str(index)})
        ET.SubElement(candidate, "connection", **{"from": "in", "to": "out", "fromLane": str(index), "toLane": str(index), "via": f":joined_0_{index}"})
        ET.SubElement(candidate, "connection", **{"from": ":joined_0", "to": "out", "fromLane": str(index), "toLane": str(index)})
    lanes, movements = [], []
    for index, y in enumerate((-2.0, 2.0)):
        ids = ("52", "26", "58", "12") if index == 0 else ("53", "27", "59", "13")
        for lane_id, role, bx, ax in ((ids[0], "ingress", -10, -20), (ids[1], "egress", -5, 0), (ids[2], "ingress", 5, 0), (ids[3], "egress", 10, 20)):
            lanes.append({"lane_id": lane_id, "lane_type": "vehicle", "direction_role": role, "shape_network": [(bx, y), (ax, y)], "junction_endpoint_network": (bx, y)})
        for source, target, xs in ((ids[0], ids[1], (-10, -5)), (ids[2], ids[3], (5, 10))):
            movements.append({"movement_id": f"{source}-{target}", "ingress_lane_id": source, "egress_lane_id": target, "intersection_part": "0", "selected_source": "official_map", "official_shape_network": [(xs[0], y), (xs[1], y)], "selected_shape_network": [(xs[0], y), (xs[1], y)]})
    plans = {"119": {"node_id": "119", "lanes": lanes, "movements": movements, "max_error_m": 0.5}}
    groups = [{"node_id": "119", "intersection_part": "0", "join_id": "joined", "source_node_ids": ["left", "middle", "right"]}]
    return original, candidate, plans, groups


def test_two_official_stages_compose_into_joined_boundary_connections():
    original, candidate, plans, groups = _fixtures()
    report = compose_official_movements(original, candidate, plans=plans, groups=groups)
    assert {tuple(row) for row in report["required_boundary_pairs"]} == {("in", 0, "out", 0), ("in", 1, "out", 1)}
    assert len(report["movement_coverage"]) == 4
    assert all(row["status"] == "pass" for row in report["movement_coverage"])
    path = next(row for row in report["boundary_paths"] if row["official_lane_sequence"] == ["52", "26", "58", "12"])
    assert "egress_0" in path["original_lane_path"]
    assert "ingress_0" in path["original_lane_path"]
    assert path["candidate_lane_path"] == ["in_0", ":joined_0_0", "out_0"]
    assert all(segment["candidate_arc_end_m"] > segment["candidate_arc_start_m"] for segment in path["movement_segments"])
    assert path["composed_shape_network"] is not None


def test_nearby_endpoints_without_source_lane_continuation_do_not_prove_coverage():
    original, candidate, plans, groups = _fixtures()
    broken = original.find("connection[@from='egress'][@fromLane='1']")
    original.remove(broken)
    report = compose_official_movements(original, candidate, plans=plans, groups=groups)
    assert ("in", 1, "out", 1) not in {tuple(row) for row in report["required_boundary_pairs"]}
    assert sum(row["status"] == "pass" for row in report["movement_coverage"]) == 2
    assert report["unresolved_continuations"]


def test_boundary_identity_alone_does_not_prove_spatial_movement_coverage():
    original, candidate, plans, groups = _fixtures()
    candidate.find("edge[@id=':joined_0']/lane[@id=':joined_0_0']").set("shape", "-10,-2 0,20 10,-2")
    report = compose_official_movements(original, candidate, plans=plans, groups=groups)
    assert ("in", 0, "out", 0) in {tuple(row) for row in report["required_boundary_pairs"]}
    affected = [row for row in report["movement_coverage"] if row["ingress_lane_id"] in {"52", "58"}]
    assert all(row["status"] == "review_required" for row in affected)
    assert any(row["geometry_status"] == "review_required" for row in report["boundary_paths"])


def test_composed_vehicle_class_must_be_allowed_on_final_boundary_lane():
    original, candidate, plans, groups = _fixtures()
    next(lane for lane in plans["119"]["lanes"] if lane["lane_id"] == "52")["allowed_vehicle_classes"] = ["bus"]
    candidate.find("edge[@id='out']/lane[@id='out_0']").set("allow", "delivery")
    report = compose_official_movements(original, candidate, plans=plans, groups=groups)
    assert ("in", 0, "out", 0) not in {tuple(row) for row in report["required_boundary_pairs"]}
    assert next(row for row in report["movement_coverage"] if row["ingress_lane_id"] == "52")["status"] == "review_required"


def test_source_context_resolves_a_nearby_but_disconnected_parallel_lane():
    original, candidate, plans, groups = _fixtures()
    edge = ET.SubElement(original, "edge", id="lookalike", **{"from": "middle", "to": "right"})
    ET.SubElement(edge, "lane", id="lookalike_0", index="0", shape="0,-1.9 5,-1.9", speed="10", length="5")
    report = compose_official_movements(original, candidate, plans=plans, groups=groups)
    assert all(row["status"] == "pass" for row in report["movement_coverage"])
    resolved = next(row for row in report["original_lane_binding_reviews"] if row["lane_id"] == "58")
    assert resolved["reason"] == "unique_original_movement_context"
    assert resolved["resolved_lane_id"] == "ingress_0"


def test_spatial_witness_can_include_the_retained_boundary_lane():
    original, candidate, plans, groups = _fixtures()
    candidate.find("edge[@id='out']/lane[@id='out_0']").set("shape", "4,-2 20,-2")
    candidate.find("edge[@id=':joined_0']/lane[@id=':joined_0_0']").set("shape", "-10,-2 4,-2")
    report = compose_official_movements(original, candidate, plans=plans, groups=groups)
    path = next(row for row in report["boundary_paths"] if row["official_lane_sequence"] == ["52", "26", "58", "12"])
    assert path["status"] == "pass"
    assert path["connection_shape_network"][-1] == (4.0, -2.0)
    segment = next(row for row in path["movement_segments"] if row["official_lane_pair"] == ["58", "12"])
    assert segment["candidate_lane_ids"] == ["out_0"]


def test_constructed_boundary_lane_without_original_alias_keeps_official_authority():
    original, candidate, plans, groups = _fixtures()
    report = compose_official_movements(original, candidate, plans=plans, groups=groups, current_lane_to_original_lane={"in_0": None})
    path = next(row for row in report["boundary_paths"] if row["official_lane_sequence"] == ["52", "26", "58", "12"])
    assert path["status"] == "pass"
    assert path["original_lane_path"] == []


def test_search_keeps_a_feasible_vehicle_class_alternative():
    from torii_sumo.core.official_movement_composition import _walks

    graph = {
        "a": [{"target": "b", "allowed_vehicle_classes": ["bus"]}, {"target": "c", "allowed_vehicle_classes": ["passenger"]}],
        "b": [{"target": "d", "allowed_vehicle_classes": ["bus"]}],
        "c": [{"target": "d", "allowed_vehicle_classes": ["passenger"]}],
        "d": [{"target": "out", "movement_token": "required", "allowed_vehicle_classes": ["passenger"]}],
    }
    paths = list(_walks("a", graph, {"out"}, "required"))
    assert [path[1] for path in paths] == [["a", "c", "d", "out"]]


def test_missing_candidate_connection_keeps_reconstruction_evidence():
    original, candidate, plans, groups = _fixtures()
    candidate.remove(candidate.find("connection[@from='in'][@fromLane='0']"))
    next(lane for lane in plans["119"]["lanes"] if lane["lane_id"] == "52")["allowed_vehicle_classes"] = ["bus"]
    report = compose_official_movements(original, candidate, plans=plans, groups=groups)
    path = next(row for row in report["boundary_paths"] if row["official_lane_sequence"] == ["52", "26", "58", "12"])
    assert path["allowed_vehicle_classes"] == ["bus"]
    assert path["actual_allowed_vehicle_classes"] == []
    assert path["original_lane_path"] == ["in_0", "egress_0", "ingress_0", "out_0"]
    assert path["original_path_vehicle_class"] == "bus"
    assert path["connection_shape_network"][0] == (-10.0, -2.0)
    assert path["connection_shape_network"][-1] == (10.0, -2.0)
    assert path["candidate_lane_path"] == []
    assert not path["topology_covered"]
    assert path["status"] == "review_required"


def test_incompatible_internal_lane_does_not_erase_proposed_permissions():
    original, candidate, plans, groups = _fixtures()
    next(lane for lane in plans["119"]["lanes"] if lane["lane_id"] == "52")["allowed_vehicle_classes"] = ["bus"]
    candidate.find("edge[@id=':joined_0']/lane[@id=':joined_0_0']").set("allow", "delivery")
    report = compose_official_movements(original, candidate, plans=plans, groups=groups)
    path = next(row for row in report["boundary_paths"] if row["official_lane_sequence"] == ["52", "26", "58", "12"])
    assert path["allowed_vehicle_classes"] == ["bus"]
    assert path["actual_allowed_vehicle_classes"] == []
    assert path["original_path_vehicle_class"] == "bus"
    assert path["connection_shape_network"]
    assert not path["topology_covered"]
    assert path["status"] == "review_required"


def test_official_bus_only_middle_lane_rejects_extra_candidate_permissions():
    original, candidate, plans, groups = _fixtures()
    next(lane for lane in plans["119"]["lanes"] if lane["lane_id"] == "26")["allowed_vehicle_classes"] = ["bus"]
    report = compose_official_movements(original, candidate, plans=plans, groups=groups)
    path = next(row for row in report["boundary_paths"] if row["official_lane_sequence"] == ["52", "26", "58", "12"])
    assert path["status"] == "review_required"
    assert path["geometry_status"] == "review_required"
    assert not path["topology_covered"]
    assert path["allowed_vehicle_classes"] == ["bus"]
    assert path["actual_allowed_vehicle_classes"] == ["bus"]
    assert "passenger" in path["candidate_allowed_vehicle_classes"]
    assert "passenger" in path["excess_vehicle_classes"]
    assert all(row["status"] == "review_required" for row in report["movement_coverage"] if row["ingress_lane_id"] in {"52", "58"})


def test_same_boundary_pair_unions_permissions_from_all_official_paths():
    original, candidate, plans, groups = _fixtures()
    lanes = {lane["lane_id"]: lane for lane in plans["119"]["lanes"]}
    lanes["26"]["allowed_vehicle_classes"] = ["bus"]
    lanes["27"]["allowed_vehicle_classes"] = ["passenger"]
    plans["119"]["movements"][2]["ingress_lane_id"] = "52"
    plans["119"]["movements"][3]["egress_lane_id"] = "12"
    for lane in candidate.iter("lane"):
        lane.set("allow", "bus passenger")
    bindings = {"joined": {"ingress": {"52": ("in", 0)}, "egress": {"12": ("out", 0)}}}
    report = compose_official_movements(original, candidate, plans=plans, groups=groups, boundary_bindings=bindings)
    paths = report["boundary_paths"]
    assert len(paths) == 2
    assert {tuple(path["allowed_vehicle_classes"]) for path in paths} == {("bus",), ("passenger",)}
    assert all(path["candidate_allowed_vehicle_classes"] == ["bus", "passenger"] for path in paths)
    assert all(path["boundary_allowed_vehicle_classes"] == ["bus", "passenger"] for path in paths)
    assert all(path["excess_vehicle_classes"] == [] for path in paths)
    assert all(path["permission_status"] == "pass" for path in paths)


def _continuous_original_fixture():
    original, candidate, plans, groups = _fixtures()
    for owner, source, target, first, last in (("left", "in", "egress", -10, -5), ("right", "ingress", "out", 5, 10)):
        edge = ET.SubElement(original, "edge", id=f":{owner}_0", function="internal")
        for index, y in enumerate((-2, 2)):
            ET.SubElement(edge, "lane", id=f":{owner}_0_{index}", index=str(index), shape=f"{first},{y} {last},{y}", speed="10", length="5")
            original.find(f"connection[@from='{source}'][@fromLane='{index}']").set("via", f":{owner}_0_{index}")
            ET.SubElement(original, "connection", **{"from": f":{owner}_0", "to": target, "fromLane": str(index), "toLane": str(index)})
    return original, candidate, plans, groups


def test_source_prefix_reaches_an_internal_official_ingress_without_extra_movements():
    original, candidate, plans, groups = _continuous_original_fixture()
    plans["119"]["movements"] = [m for m in plans["119"]["movements"] if m["ingress_lane_id"] in {"58", "59"}]
    report = compose_official_movements(original, candidate, plans=plans, groups=groups)
    assert len(report["movement_coverage"]) == 2
    assert all(row["status"] == "pass" for row in report["movement_coverage"])
    path = next(row for row in report["boundary_paths"] if row["official_lane_sequence"] == ["58", "12"])
    assert path["connection"] == ["in", 0, "out", 0]
    assert path["connection_shape_network"][0] == (-10.0, -2.0)
    assert len(path["movement_segments"]) == 1
    prefix = path["source_derived_segments"][0]
    assert prefix["kind"] == "source_prefix"
    assert prefix["original_lane_path"] == ["in_0", ":left_0_0", "egress_0", "ingress_0"]
    assert prefix["geometry_anchor_error_m"] == 0


def test_source_suffix_reaches_boundary_without_inventing_an_official_movement():
    original, candidate, plans, groups = _continuous_original_fixture()
    plans["119"]["movements"] = [m for m in plans["119"]["movements"] if m["ingress_lane_id"] in {"52", "53"}]
    report = compose_official_movements(original, candidate, plans=plans, groups=groups)
    assert len(report["movement_coverage"]) == 2
    assert all(row["status"] == "pass" for row in report["movement_coverage"])
    path = next(row for row in report["boundary_paths"] if row["official_lane_sequence"] == ["52", "26"])
    suffix = path["source_derived_segments"][0]
    assert suffix["kind"] == "source_suffix"
    assert suffix["original_lane_path"] == ["egress_0", "ingress_0", ":right_0_0", "out_0"]
    assert path["connection_shape_network"][-1] == (10.0, -2.0)


def test_source_witness_clipping_keeps_later_lanes_after_earlier_near_intersection():
    from torii_sumo.core.official_movement_composition import _clip_witness_shapes

    pieces = [[(-20, 0), (-10, 0)], [(-10, 0), (5, 0), (10, 10), (0, 1)], [(0, 1), (5, 1)]]
    curve, error, gap = _clip_witness_shapes(pieces, start=(-10, 0), end=(5, 0))
    assert (10, 10) in curve
    assert (0, 1) in curve
    assert curve[-1] == (5, 0)
    assert error == 1.0  # A whole-polyline nearest fit would cut early and claim zero.
    assert gap == 0


def test_source_witness_keeps_explicit_final_endpoint_after_an_earlier_crossing():
    from torii_sumo.core.official_movement_composition import _clip_witness_shapes

    curve, error, gap = _clip_witness_shapes(
        [[(-10, 0), (5, 0), (10, 10), (5, 0)]], start=(-10, 0), end=(5, 0)
    )
    assert (10, 10) in curve
    assert curve[-1] == (5, 0)
    assert error == 0
    assert gap == 0


def test_source_prefix_needs_the_interior_path_not_only_a_joined_connection():
    original, candidate, plans, groups = _continuous_original_fixture()
    plans["119"]["movements"] = [m for m in plans["119"]["movements"] if m["ingress_lane_id"] == "58"]
    original.remove(original.find("connection[@from='egress'][@fromLane='0']"))
    report = compose_official_movements(original, candidate, plans=plans, groups=groups)
    assert report["required_boundary_pairs"] == []
    assert report["movement_coverage"][0]["status"] == "review_required"


def test_source_prefix_tracks_old_internal_sections_carried_by_extended_boundary_lane():
    original, candidate, plans, groups = _continuous_original_fixture()
    plans["119"]["movements"] = [m for m in plans["119"]["movements"] if m["ingress_lane_id"] == "58"]
    candidate.find("edge[@id='in']/lane[@id='in_0']").set("shape", "-20,-2 2,-2")
    candidate.find("edge[@id=':joined_0']/lane[@id=':joined_0_0']").set("shape", "2,-2 10,-2")
    report = compose_official_movements(original, candidate, plans=plans, groups=groups, boundary_bindings={"joined": {"ingress": {}, "egress": {"12": ("out", 0)}}})
    assert report["movement_coverage"][0]["status"] == "pass"
    prefix = report["boundary_paths"][0]["source_derived_segments"][0]
    assert prefix["curve_original_lane_path"] == ["ingress_0"]
    carried = {row["original_lane_id"]: row for row in prefix["carried_by_boundary"]}
    assert carried["ingress_0"]["end_position_m"] == 2
    assert carried[":left_0_0"]["end_position_m"] == 5
    assert prefix["shape_network"] == [(2.0, -2.0), (5.0, -2.0)]


def test_extended_boundary_does_not_erase_a_later_source_loop_near_its_endpoint():
    original, candidate, plans, groups = _continuous_original_fixture()
    plans["119"]["movements"] = [m for m in plans["119"]["movements"] if m["ingress_lane_id"] == "58"]
    original.find("edge[@id=':left_0']/lane[@id=':left_0_0']").set("shape", "-10,-2 5,-2 10,10 -5,-2")
    candidate.find("edge[@id='in']/lane[@id='in_0']").set("shape", "-20,-2 5,-2")
    candidate.find("edge[@id=':joined_0']/lane[@id=':joined_0_0']").set("shape", "5,-2 10,-2")
    report = compose_official_movements(original, candidate, plans=plans, groups=groups, boundary_bindings={"joined": {"ingress": {}, "egress": {"12": ("out", 0)}}})
    assert report["boundary_paths"]
    path = report["boundary_paths"][0]
    assert (10.0, 10.0) in path["connection_shape_network"]
    assert path["geometry_status"] == "review_required"


def _internal_b_carrier_fixture():
    original = ET.fromstring('''<net>
      <edge id="in" from="outside-in" to="a"><lane id="in_0" allow="bus" shape="-20,0 -1,0"/></edge>
      <edge id=":a_0" function="internal"><lane id=":a_0_0" allow="bus" shape="-1,0 0,0"/></edge>
      <edge id="middle" from="a" to="b"><lane id="middle_0" allow="bus" shape="0,0 1,0"/></edge>
      <edge id=":b_0" function="internal"><lane id=":b_0_0" allow="bus" shape="1,0 30,0"/></edge>
      <edge id="after" from="b" to="c"><lane id="after_0" allow="bus" shape="30,0 40,0"/></edge>
      <edge id="out" from="c" to="outside-out"><lane id="out_0" allow="bus" shape="40,0 60,0"/></edge>
      <connection from="in" to="middle" fromLane="0" toLane="0" via=":a_0_0" dir="s"/>
      <connection from=":a_0" to="middle" fromLane="0" toLane="0"/>
      <connection from="middle" to="after" fromLane="0" toLane="0" via=":b_0_0" dir="s"/>
      <connection from=":b_0" to="after" fromLane="0" toLane="0"/>
      <connection from="after" to="out" fromLane="0" toLane="0" dir="s"/>
    </net>''')
    candidate = ET.fromstring('''<net>
      <edge id="in" from="outside-in" to="J"><lane id="in_0" allow="bus" shape="-20,0 -1,0"/></edge>
      <edge id="out" from="J" to="outside-out"><lane id="out_0" allow="bus" shape="40,0 60,0"/></edge>
      <edge id=":J_0" function="internal"><lane id=":J_0_0" allow="bus" shape="-1,0 40,0"/></edge>
      <connection from="in" to="out" fromLane="0" toLane="0" via=":J_0_0"/>
      <connection from=":J_0" to="out" fromLane="0" toLane="0"/>
    </net>''')
    plan = {"lanes": [
        {"lane_id": "61", "lane_type": "vehicle", "direction_role": "ingress", "shape_network": [(0, 0), (15, 0)], "junction_endpoint_network": (15, 0), "allowed_vehicle_classes": ["bus"]},
        {"lane_id": "out", "lane_type": "vehicle", "direction_role": "egress", "shape_network": [(40, 0), (60, 0)], "junction_endpoint_network": (40, 0), "allowed_vehicle_classes": ["bus"]},
    ], "movements": [{"movement_id": "33", "intersection_part": "0", "ingress_lane_id": "61", "egress_lane_id": "out", "official_shape_network": [(15, 0), (40, 0)]}], "max_error_m": 3}
    groups = [{"node_id": "118", "intersection_part": "0", "join_id": "J", "source_node_ids": ["a", "b", "c"]}]
    return original, candidate, {"118": plan}, groups


def test_source_prefix_keeps_bound_internal_b_carrier_and_normal_lane_identity():
    original, candidate, plans, groups = _internal_b_carrier_fixture()
    before = ET.tostring(original), ET.tostring(candidate)
    report = compose_official_movements(original, candidate, plans=plans, groups=groups)
    binding = next(row for row in report["original_lane_binding_reviews"] if row["lane_id"] == "61")
    assert binding["resolved_lane_id"] == "middle_0"
    assert binding["source_internal_lane_id"] == ":b_0_0"
    assert binding["boundary_projection_error_m"] == 0
    assert report["movement_coverage"][0]["status"] == "pass"
    prefix = report["source_boundary_extensions"][0]
    assert prefix["original_lane_path"] == ["in_0", ":a_0_0", "middle_0", ":b_0_0"]
    assert prefix["shape_network"] == [(-1.0, 0.0), (0.0, 0.0), (1.0, 0.0), (15.0, 0.0)]
    assert prefix["source_lane_geometry_error_m"] == 0
    assert prefix["geometry_anchor_error_m"] == 0
    assert prefix["source_chain_gap_m"] == 0
    assert (ET.tostring(original), ET.tostring(candidate)) == before


def test_source_suffix_keeps_bound_internal_b_carrier():
    original, candidate, plans, groups = _internal_b_carrier_fixture()
    for root in (original, candidate):
        connections = [dict(row.attrib) for row in root.findall("connection") if not row.get("from").startswith(":")]
        for row in list(root.findall("connection")):
            root.remove(row)
        for edge in root.findall("edge"):
            if edge.get("function") != "internal":
                edge.attrib["from"], edge.attrib["to"] = edge.get("to"), edge.get("from")
            for lane in edge.findall("lane"):
                lane.set("shape", " ".join(reversed(lane.get("shape").split())))
        for row in connections:
            row["from"], row["to"] = row["to"], row["from"]
            ET.SubElement(root, "connection", row)
            if row.get("via"):
                ET.SubElement(root, "connection", **{"from": row["via"].rsplit("_", 1)[0], "to": row["to"], "fromLane": "0", "toLane": "0"})
    for lane in plans["118"]["lanes"]:
        lane["direction_role"] = "egress" if lane["lane_id"] == "61" else "ingress"
    movement = plans["118"]["movements"][0]
    movement.update(ingress_lane_id="out", egress_lane_id="61", official_shape_network=[(40, 0), (15, 0)])
    report = compose_official_movements(original, candidate, plans=plans, groups=groups)
    assert report["movement_coverage"][0]["status"] == "pass"
    suffix = report["source_boundary_extensions"][0]
    assert suffix["kind"] == "source_suffix"
    assert suffix["original_lane_path"] == [":b_0_0", "middle_0", ":a_0_0", "in_0"]
    assert suffix["shape_network"] == [(15.0, 0.0), (1.0, 0.0), (0.0, 0.0), (-1.0, 0.0)]


@pytest.mark.parametrize("fault", ["different_owner", "disconnected", "wrong_mode", "reverse_internal", "real_geometry_deviation"])
def test_source_b_carrier_does_not_override_path_permission_or_geometry(fault):
    original, candidate, plans, groups = _internal_b_carrier_fixture()
    edge = original.find("edge[@id=':b_0']")
    if fault == "different_owner":
        edge.set("id", ":other_0")
        edge[0].set("id", ":other_0_0")
        original.find("connection[@from='middle']").set("via", ":other_0_0")
        original.find("connection[@from=':b_0']").set("from", ":other_0")
        ET.SubElement(original, "junction", id="other")
    elif fault == "disconnected":
        original.remove(original.find("connection[@from='middle']"))
    elif fault == "wrong_mode":
        edge[0].set("allow", "passenger")
    elif fault == "reverse_internal":
        edge[0].set("shape", "1,0 -5,0 30,0")
    else:
        edge[0].set("shape", "1,0 8,8 30,0")
    report = compose_official_movements(original, candidate, plans=plans, groups=groups)
    assert report["movement_coverage"][0]["status"] == "review_required"
    if fault == "reverse_internal":
        assert any(row["reason"] == "source_internal_geometry_reverses_a_straight_continuation" for row in report["source_boundary_extension_reviews"])
