import xml.etree.ElementTree as ET

import pytest

from torii_sumo.core.candidate_contracts import file_sha256
from torii_sumo.road_network.osm_road_chain import inspect_osm_road_chain


TAGS = {"highway": "secondary", "name": "Example", "oneway": "yes", "lanes": "2", "maxspeed": "50"}


def _source(tmp_path, *, second_nodes=(3, 4), second_tags=None, extra_way=None,
            node_tags=None, missing_node=None, relation=None):
    root = ET.Element("osm", version="0.6")
    coordinates = {1: (10, 53), 2: (10.0001, 53), 3: (10.0002, 53), 4: (10.0003, 53),
                   5: (10.0002, 53.0001), 6: (10.0002, 53), 7: (10.0002, 52.9999)}
    for node_id, (lon, lat) in coordinates.items():
        if node_id == missing_node:
            continue
        node = ET.SubElement(root, "node", id=str(node_id), lon=str(lon), lat=str(lat))
        for key, value in (node_tags or {}).get(node_id, {}).items():
            ET.SubElement(node, "tag", k=key, v=value)
    records = [("10", (1, 2, 3), TAGS), ("20", second_nodes, TAGS | (second_tags or {}))]
    if extra_way:
        records.append(extra_way)
    for way_id, refs, tags in records:
        way = ET.SubElement(root, "way", id=way_id)
        for ref in refs:
            ET.SubElement(way, "nd", ref=str(ref))
        for key, value in tags.items():
            ET.SubElement(way, "tag", k=key, v=value)
    if relation:
        r = ET.SubElement(root, "relation", id="100")
        ET.SubElement(r, "tag", k="type", v=relation)
        ET.SubElement(r, "member", type="way", ref="10", role="from")
        ET.SubElement(r, "member", type="node", ref="3", role="via")
        ET.SubElement(r, "member", type="way", ref="20", role="to")
        ET.SubElement(r, "tag", k="restriction" if relation == "restriction" else "connectivity", v="no_straight_on" if relation == "restriction" else "1:1|2:2")
    path = tmp_path / "roads.osm.xml"
    ET.ElementTree(root).write(path, encoding="utf-8", xml_declaration=True)
    return {"path": str(path), "sha256": file_sha256(path), "data_year": 2013}


def _boundary(result, node_id):
    return next(row for row in result["boundaries"] if row["node_id"] == str(node_id))


def test_two_fragments_form_a_directed_continuation_without_losing_shape_points(tmp_path):
    source = _source(tmp_path)
    result = inspect_osm_road_chain(source, ["10", "20"])
    assert result["status"] == "pass"
    assert result["axis_node_ids"] == ["1", "2", "3", "4"]
    assert len(result["axis_lonlat"]) == 4
    assert result["axis_length_m"] > 0
    assert _boundary(result, 3)["classification"] == "continuation_candidate"
    assert _boundary(result, 2)["classification"] == "continuation_candidate"
    assert result["aggregation_allowed"] is True
    assert result["source_immutable"] is True
    assert result["ways"][0]["node_ids"] == ["1", "2", "3"]
    assert result["ways"][1]["node_ids"] == ["3", "4"]


@pytest.mark.parametrize("change,key", [({"lanes": "3"}, "lanes"), ({"turn:lanes": "left|through"}, "turn:lanes"),
                                        ({"change:lanes": "no|yes"}, "change:lanes"), ({"access": "private"}, "access"),
                                        ({"maxspeed": "30"}, "maxspeed"), ({"width": "8"}, "width"),
                                        ({"cycleway:right": "lane"}, "cycleway:right"), ({"oneway": "no"}, "oneway"),
                                        ({"bridge": "yes", "layer": "1"}, "bridge"), ({"tunnel": "yes"}, "tunnel")])
def test_physical_attribute_changes_are_mandatory_boundaries(tmp_path, change, key):
    result = inspect_osm_road_chain(_source(tmp_path, second_tags=change), ["10", "20"])
    boundary = _boundary(result, 3)
    assert boundary["mandatory_boundary"] is True
    assert key in boundary["attribute_changes"]
    assert result["aggregation_allowed"] is False


def test_real_side_road_is_preserved_but_a_grade_separated_geometric_crossing_is_not_a_side_road(tmp_path):
    source = _source(tmp_path, extra_way=("30", (3, 5), {"highway": "residential"}))
    result = inspect_osm_road_chain(source, ["10", "20"])
    assert _boundary(result, 3)["side_road_way_ids"] == ["30"]
    assert _boundary(result, 3)["mandatory_boundary"] is True
    source = _source(tmp_path, extra_way=("30", (7, 6, 5), {"highway": "primary", "bridge": "yes", "layer": "1"}))
    result = inspect_osm_road_chain(source, ["10", "20"])
    assert _boundary(result, 3)["side_road_way_ids"] == []
    assert _boundary(result, 3)["classification"] == "continuation_candidate"


@pytest.mark.parametrize("tag", ["traffic_signals", "crossing", "stop"])
def test_node_control_features_require_a_boundary(tmp_path, tag):
    result = inspect_osm_road_chain(_source(tmp_path, node_tags={2: {"highway": tag}}), ["10", "20"])
    assert _boundary(result, 2)["mandatory_boundary"] is True
    assert "node_control_or_crossing" in _boundary(result, 2)["reasons"]


@pytest.mark.parametrize("kind", ["restriction", "connectivity"])
def test_relation_membership_and_protected_boundary_are_retained(tmp_path, kind):
    result = inspect_osm_road_chain(_source(tmp_path, relation=kind), ["10", "20"])
    assert result["ways"][0]["relation_membership"][0]["relation_id"] == "100"
    assert _boundary(result, 3)["protected_relation_ids"] == ["100"]
    assert _boundary(result, 3)["mandatory_boundary"] is True


def test_reverse_oneway_and_missing_nodes_never_produce_a_fake_axis(tmp_path):
    result = inspect_osm_road_chain(_source(tmp_path, second_nodes=(4, 3)), ["10", "20"])
    assert result["status"] == "review_required"
    assert result["axis_lonlat"] == []
    assert any(row["reason"] == "oneway_reverse_forbidden" for row in result["reasons"])
    result = inspect_osm_road_chain(_source(tmp_path, missing_node=4), ["10", "20"])
    assert result["status"] == "review_required"
    assert result["axis_lonlat"] == []
    assert result["ways"][1]["node_ids"] == ["3", "4"]
    assert result["ways"][1]["missing_node_ids"] == ["4"]


def test_single_way_can_reverse_only_when_its_direction_permits(tmp_path):
    source = _source(tmp_path, second_tags={"oneway": "no"})
    result = inspect_osm_road_chain(source, ["20"], start_node_id="4")
    assert result["axis_node_ids"] == ["4", "3"]
    assert result["ways"][0]["reversed"] is True
    refused = inspect_osm_road_chain(source, ["10"], start_node_id="3")
    assert refused["status"] == "review_required" and refused["axis_lonlat"] == []


@pytest.mark.parametrize("refs", [(5, 4), (2, 4), (3, 2, 4)])
def test_disconnected_internal_or_multiple_shared_nodes_require_review(tmp_path, refs):
    result = inspect_osm_road_chain(_source(tmp_path, second_nodes=refs), ["10", "20"])
    assert result["status"] == "review_required"
    assert result["axis_lonlat"] == []


def test_invalid_request_or_source_identity_raises(tmp_path):
    source = _source(tmp_path)
    for ids in ([], ["10", "10"], "10", [10]):
        with pytest.raises(ValueError):
            inspect_osm_road_chain(source, ids)
    with pytest.raises(ValueError, match="SHA-256"):
        inspect_osm_road_chain({**source, "sha256": "0" * 64}, ["10"])
