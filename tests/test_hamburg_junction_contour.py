import importlib
from xml.etree import ElementTree as ET

import pytest


def network(before, width=2, mouths=False):
    root = ET.Element("net")
    ET.SubElement(root, "junction", id="j", shape=before)
    edge = ET.SubElement(root, "edge", id=":j_0", function="internal")
    ET.SubElement(edge, "lane", id=":j_0_0", shape="0,0 10,0", width=str(width))
    if mouths:
        for name, start, end, shape in (("in", "w", "j", "-10,0 0,0"), ("out", "j", "e", "10,0 20,0")):
            edge = ET.SubElement(root, "edge", id=name, **{"from": start, "to": end})
            ET.SubElement(edge, "lane", id=name + "_0", shape=shape, width=str(width))
    return root


def module():
    return importlib.import_module("torii_sumo.core.hamburg_junction_contour")


def test_fused_surface_uses_lane_width_and_keeps_mouths_without_mutating_source():
    root = network("0,-2.25 5,-12 10,-2.25 10,2.25 5,12 0,2.25", width=4.5, mouths=True)
    original = ET.tostring(root)
    result = module().propose_fused_junction_contour(root, "j")
    assert result["changed"]
    assert result["method"] == "smooth_surface_with_preserved_mouth_edges/v2"
    assert result["after_area_m2"] < result["before_area_m2"] * .9
    assert result["surface_audit"]["preservation_pass"]
    assert result["surface_audit"]["mouth_coverage_status"] == "pass"
    assert result["surface_audit"]["support_widths_m"] == {":j_0_0": 4.5}
    assert ET.tostring(root) == original


def test_smooth_contour_rounds_corners_without_flared_blocks_at_road_mouths():
    from shapely import LineString, Point, Polygon

    root = network("-20,-2 -2,-20 2,-20 20,-2 20,2 2,20 -2,20 -20,2", width=3.2)
    root.find("edge/lane").set("shape", "-20,0 20,0")
    edge = ET.SubElement(root, "edge", id=":j_1", function="internal")
    ET.SubElement(edge, "lane", id=":j_1_0", shape="0,-20 0,20", width="3.2")
    for name, points in (("w", "-40,0 -20,0"), ("e", "40,0 20,0"),
                         ("n", "0,40 0,20"), ("s", "0,-40 0,-20")):
        edge = ET.SubElement(root, "edge", id=name, **{"from": name, "to": "j"})
        ET.SubElement(edge, "lane", id=name + "_0", shape=points, width="3.2")
    result = module().propose_fused_junction_contour(root, "j", corner_radius_m=8)
    outline = Polygon(result["proposed_shape"])
    assert result["changed"] and result["surface_audit"]["preservation_pass"]
    assert outline.covers(Point(4, 4))  # A continuous rounded corner, beyond a narrow crossing of lane strips.
    section = outline.intersection(LineString([(-18, -30), (-18, 30)]))
    assert section.bounds[1] >= -2.1 and section.bounds[3] <= 2.1
    for center in ((-20, 0), (20, 0), (0, -20), (0, 20)):
        assert outline.boundary.distance(Point(center)) < .01


def test_fused_surface_retains_an_invalid_source_instead_of_erasing_its_parts():
    root = network("0,-2 10,2 0,2 10,-2", mouths=True)
    result = module().propose_fused_junction_contour(root, "j")
    assert not result["changed"]
    assert result["status"] == "blocked"
    assert result["proposed_shape"] == result["source_shape"]


def test_smooth_contour_repairs_an_internal_loop_without_discarding_source_parts():
    root = network("-2,-4 12,-4 12,4 -2,4 -2,-4 2,-2 4,-2 4,2 2,2 2,-2 -2,-4", mouths=True)
    original = ET.tostring(root)
    result = module().propose_fused_junction_contour(root, "j")
    assert result["changed"]
    assert result["method"] == "smooth_surface_with_preserved_mouth_edges/v2"
    assert result["source_polygon_repair"]["no_source_component_discarded"]
    assert result["source_polygon_repair"]["filled_unclassified_loop_area_m2"] == 8
    assert result["surface_audit"]["preservation_pass"]
    assert ET.tostring(root) == original


@pytest.mark.parametrize("radius", [True, 0, float("nan")])
def test_smooth_contour_requires_a_finite_positive_corner_radius(radius):
    with pytest.raises(ValueError, match="junction_corner_radius_m"):
        module().propose_fused_junction_contour(network("0,-2 10,-2 10,2 0,2"), "j", corner_radius_m=radius)


@pytest.mark.parametrize("lower,expected", [(-1.496, "pass"), (-1.494, "blocked")])
def test_actual_lane_width_decides_the_99_and_101_mm_gap(lower, expected):
    root = network("-1,-2 11,-2 11,2 -1,2", width=3.19)
    result = module().audit_junction_contour(root, "j", [(-1, lower), (11, lower), (11, 2), (-1, 2)])
    assert result["regression_status"] == expected
    assert result["geometry_preservation_pass"] is (expected == "pass")
    assert result["support_widths_m"] == {":j_0_0": 3.19}
    assert result["tolerance_m"] == .1


def test_existing_gap_is_preserved_but_not_called_complete_coverage():
    root = network("0,-0.5 10,-0.5 10,0.5 0,0.5")
    result = module().audit_junction_contour(root, "j", [(0, -.5), (10, -.5), (10, .5), (0, .5)])
    assert result["regression_status"] == "pass"
    assert result["absolute_coverage_status"] == "review_required"
    assert result["quality_status"] == "review_required"


def test_mouth_inside_polygon_is_covered_and_cut_position_is_a_separate_check():
    root = network("0,-1 10,-1 10,1 0,1", mouths=True)
    result = module().audit_junction_contour(root, "j", [(-1, -1), (11, -1), (11, 1), (-1, 1)])
    assert result["mouth_coverage_status"] == "pass"
    assert result["external_cut_status"] == "not_checked"
    assert not result["contained_within_source_tolerance"]
    assert not result["preservation_pass"]  # Coverage is fine; the expanded shape is not.


@pytest.mark.parametrize("extra,expected", [(.05, True), (.11, False)])
def test_compiled_boundary_expansion_is_checked_separately(extra, expected):
    root = network("0,-2 10,-2 10,2 0,2")
    result = module().audit_junction_contour(root, "j", [(-extra, -2), (10 + extra, -2), (10 + extra, 2), (-extra, 2)])
    assert result["contained_within_source_tolerance"] is expected
    assert result["preservation_pass"] is expected


@pytest.mark.parametrize("partial_gap", [False, True])
def test_old_tolerance_cannot_accumulate_into_a_new_199_mm_gap(partial_gap):
    before = "-1,-1.496 11,-1.496 11,2 -1,2"
    after = [(-1, -1.396), (11, -1.396), (11, 2), (-1, 2)]
    if partial_gap:
        before = "-1,-1.496 5,-1.496 5,-0.5 11,-0.5 11,2 -1,2"
        after = [(-1, -1.396), (5, -1.396), (5, -.5), (11, -.5), (11, 2), (-1, 2)]
    result = module().audit_junction_contour(network(before, width=3.19), "j", after)
    assert not result["preservation_pass"]
    assert result["absolute_coverage_status"] == "review_required"


def test_regression_does_not_require_preserving_an_unqualified_square_corner():
    root = network("0,0 10,0 10,10 0,10", width=.002)
    root.find("edge/lane").set("shape", "-.09,-.09 -.08,-.08")
    result = module().audit_junction_contour(root, "j", [(0, .08), (.08, 0), (10, 0), (10, 10), (0, 10)])
    assert result["preservation_pass"]
    assert result["absolute_coverage_status"] == "review_required"


def test_continuous_coverage_accepts_two_boundary_capsules_covering_one_cell():
    mod = module()
    polygon = [(0, 0), (10, 0), (10, 3), (3, 3), (3, 10), (0, 10)]
    triangles = [(part, mod._bbox(part)) for part in mod._triangulate_polygon(polygon)]
    cell = [(3.09, 3.109), (3.109, 3.09), (3.09, 3.09)]
    assert mod._coverage(cell, polygon, triangles, False)["status"] == "pass"


def test_proposer_only_removes_a_proven_empty_ear_and_does_not_mutate_xml():
    root = network("0,-2 10,-2 10,2 5,5 0,2", mouths=True)
    original = ET.tostring(root)
    result = module().propose_junction_contour(root, "j")
    assert result["status"] == "pass"
    assert result["changed"]
    assert result["after_area_m2"] < result["before_area_m2"]
    assert result["checks"]["zero_protected_intersection"]
    assert result["checks"]["contained_within_source_tolerance"]
    assert ET.tostring(root) == original
    audit = module().audit_junction_contour(root, "j", result["proposed_shape"])
    assert audit["geometry_preservation_pass"]


@pytest.mark.parametrize("proposer", ["propose_junction_contour", "propose_fused_junction_contour"])
def test_proposer_keeps_disconnected_lane_regions_instead_of_selecting_the_largest(proposer):
    root = network("-1,-4 11,-4 11,4 5,7 -1,4")
    root.find("edge/lane").set("shape", "0,-2 10,-2")
    edge = ET.SubElement(root, "edge", id=":j_1", function="internal")
    ET.SubElement(edge, "lane", id=":j_1_0", shape="0,2 10,2", width="2")
    result = getattr(module(), proposer)(root, "j")
    assert result["protected_lane_ids"] == [":j_0_0", ":j_1_0"]
    audit = module().audit_junction_contour(root, "j", result["proposed_shape"])
    assert audit["absolute_coverage_status"] == "pass"


def test_partial_ears_can_tighten_a_rectangle_without_cutting_the_lane():
    root = network("-1,-2 11,-2 11,2 -1,2", mouths=True)
    result = module().propose_junction_contour(root, "j")
    assert result["changed"]
    assert result["after_area_m2"] < result["before_area_m2"]
    assert module().audit_junction_contour(root, "j", result["proposed_shape"])["preservation_pass"]


def test_bicycle_surface_and_pedestrian_mouth_are_not_empty_space():
    root = network("0,-2 10,-2 10,2 5,5 0,2", mouths=True)
    edge = ET.SubElement(root, "edge", id=":j_b", function="internal")
    ET.SubElement(edge, "lane", id=":j_b_0", shape="4,3 6,3", width="1", allow="bicycle")
    edge = ET.SubElement(root, "edge", id="walk-in", **{"from": "n", "to": "j"})
    ET.SubElement(edge, "lane", id="walk-in_0", shape="5,15 5,5", width="2", allow="pedestrian")
    result = module().propose_junction_contour(root, "j")
    assert ":j_b_0" in result["protected_lane_ids"]
    assert result["protected_mouth_count"] == 3
    assert (5.0, 5.0) in [tuple(p) for p in result["proposed_shape"]]
    assert module().audit_junction_contour(root, "j", result["proposed_shape"])["absolute_coverage_status"] == "pass"


def test_walkingarea_is_not_misread_as_a_lane_centerline():
    root = network("0,-2 10,-2 10,2 5,5 0,2")
    edge = ET.SubElement(root, "edge", id=":j_w0", function="walkingarea")
    ET.SubElement(edge, "lane", id=":j_w0_0", shape="3,2 7,2 7,4 3,4", width="2", allow="pedestrian")
    result = module().propose_junction_contour(root, "j")
    assert not result["changed"]
    assert result["status"] == "review_required"
    assert "walkingarea" in " ".join(result["reasons"])


def test_proposer_preserves_tolerance_neighbourhood_at_mouth_ends():
    root = ET.Element("net")
    ET.SubElement(root, "junction", id="j", shape="-2,1.05 2,1.05 2,4 -2,4")
    edge = ET.SubElement(root, "edge", id=":j_0", function="internal")
    ET.SubElement(edge, "lane", id=":j_0_0", shape="-1,3 1,3", width=".2")
    edge = ET.SubElement(root, "edge", id="in", **{"from": "w", "to": "j"})
    ET.SubElement(edge, "lane", id="in_0", shape="-10,0 0,0", width="2")
    result = module().propose_junction_contour(root, "j")
    assert module().audit_junction_contour(root, "j", result["proposed_shape"])["preservation_pass"]


def test_invalid_baseline_is_retained_with_a_diagnostic():
    root = network("0,-2 10,2 0,2 10,-2")
    result = module().propose_junction_contour(root, "j")
    assert result["status"] == "blocked"
    assert not result["changed"]
    assert result["proposed_shape"] == result["source_shape"]
    assert result["before_area_m2"] is None


def test_collinear_sampling_is_not_a_geometry_change():
    root = network("0,-1 10,-1 10,1 0,1")
    result = module().audit_junction_contour(root, "j", [(0, -1), (5, -1), (10, -1), (10, 1), (0, 1)])
    assert result["same_geometry"]
    assert result["geometry_preservation_pass"]


@pytest.mark.parametrize("offset,expected", [(.01, True), (.2, False)])
def test_small_compiled_rounding_at_a_concave_corner_is_continuously_bounded(offset, expected):
    root = network("0,0 10,0 10,3 3,3 3,10 0,10", width=1)
    root.find("edge/lane").set("shape", "1,1 9,1")
    proposal = [(0, 0), (10, 0), (10, 3), (3 + offset, 3 + offset), (3, 10), (0, 10)]
    result = module().audit_junction_contour(root, "j", proposal)
    assert result["contained_within_source_tolerance"] is expected
    assert result["preservation_pass"] is expected


def test_proposals_use_the_centimetre_grid_that_netconvert_writes():
    root = network("0,-2 10,-2 10,2 5,5 0,2", mouths=True)
    result = module().propose_junction_contour(root, "j")
    assert result["changed"]
    assert all(abs(value - round(value, 2)) < 1e-9 for point in result["proposed_shape"] for value in point)
    assert module().audit_junction_contour(root, "j", result["proposed_shape"])["preservation_pass"]


def test_a_notch_between_original_sample_points_is_not_certified_as_covered():
    root = network("0,0 10,0 10,10 0,10", width=10)
    root.find("edge/lane").set("shape", "0,5 10,5")
    notch = [(0, 0), (10, 0), (10, 10), (3.2, 10), (3.2, 5), (2.8, 5), (2.8, 10), (0, 10)]
    result = module().audit_junction_contour(root, "j", notch)
    assert not result["preservation_pass"]
