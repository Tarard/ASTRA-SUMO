from __future__ import annotations

import xml.etree.ElementTree as ET

import pytest

from astra_sumo.core.network_geometry_freeze import compare_frozen_geometry, prepare_geometry_frozen_input


def _network(tmp_path):
    path = tmp_path / "source.net.xml"
    path.write_text('''<net><type id="road" sidewalkWidth="2" bikeLaneWidth="1"/>
      <edge id="outside" from="a" to="b"><lane id="outside_0" index="0" length="10" shape="0,0 10,0"/></edge>
      <edge id="inside" from="b" to="c"><lane id="inside_0" index="0" length="10" shape="10,0 20,0"/></edge>
      <junction id="a" shape="0,-1 0,1 1,1"/><junction id="b" shape="9,-1 9,1 11,1"/>
      <junction id="c" shape="19,-1 19,1 21,1"/></net>''', encoding="utf-8")
    return path


def test_freeze_keeps_source_bytes_and_marks_only_undeclared_geometry(tmp_path):
    source = _network(tmp_path)
    before = source.read_bytes()
    output = tmp_path / "input.net.xml"
    report = prepare_geometry_frozen_input(source_file=source, output_file=output,
        mutable_edge_ids=frozenset({"inside"}), mutable_junction_ids=frozenset({"c"}))
    root = ET.parse(output).getroot()
    assert root.find("edge/lane").get("customShape") == "true"
    assert root.findall("edge")[1].find("lane").get("customShape") is None
    assert root.find("type").get("sidewalkWidth") is None
    assert report["frozen_lane_ids"] == ["outside_0"]
    assert len(report["removed_lane_synthesis_defaults"]) == 2
    assert source.read_bytes() == before


def test_comparison_catches_native_reclipping_despite_custom_shape_markers(tmp_path):
    source = _network(tmp_path)
    output = tmp_path / "candidate.net.xml"
    prepare_geometry_frozen_input(source_file=source, output_file=output)
    tree = ET.parse(output)
    tree.getroot().find("edge/lane").set("shape", "0,0 11,0")
    tree.write(output, encoding="utf-8")
    report = compare_frozen_geometry(source_file=source, candidate_file=output)
    assert report["status"] == "blocked"
    assert report["outside_scope_geometry"]["lanes"]["changed"] == ["outside_0"]


@pytest.mark.parametrize("changed_z", [False, True])
def test_xy_metrics_accept_mixed_zero_z_vertices_without_hiding_elevation_changes(tmp_path, changed_z):
    source = _network(tmp_path)
    tree = ET.parse(source)
    tree.getroot().find("edge/lane").set("shape", "0,0 5,0,2 10,0")
    tree.write(source, encoding="utf-8")
    candidate = tmp_path / "candidate.net.xml"
    if changed_z:
        tree.getroot().find("edge/lane").set("shape", "0,0 5,0,3 10,0")
    tree.write(candidate, encoding="utf-8")
    result = compare_frozen_geometry(source_file=source, candidate_file=candidate)
    assert result["status"] == ("blocked" if changed_z else "pass")
    assert result["max_outside_lane_shape_displacement_xy_m"] == 0
    assert result["outside_scope_geometry"]["lanes"]["changed"] == (["outside_0"] if changed_z else [])


def test_declared_shape_change_keeps_outside_comparison_exact(tmp_path):
    source = _network(tmp_path)
    output = tmp_path / "candidate.net.xml"
    prepare_geometry_frozen_input(source_file=source, output_file=output, mutable_edge_ids=frozenset({"inside"}))
    tree = ET.parse(output)
    tree.getroot().findall("edge")[1].find("lane").set("shape", "10,0 22,0")
    tree.write(output, encoding="utf-8")
    assert compare_frozen_geometry(source_file=source, candidate_file=output,
                                   mutable_edge_ids=frozenset({"inside"}),
                                   mutable_junction_ids=frozenset({"c"}))["status"] == "pass"


def test_mutable_road_does_not_authorize_moving_its_fixed_remote_endpoint(tmp_path):
    source = _network(tmp_path)
    output = tmp_path / "candidate.net.xml"
    prepare_geometry_frozen_input(source_file=source, output_file=output, mutable_edge_ids=frozenset({"inside"}))
    tree = ET.parse(output)
    tree.getroot().findall("edge")[1].find("lane").set("shape", "10,0 22,0")
    tree.write(output, encoding="utf-8")
    report = compare_frozen_geometry(source_file=source, candidate_file=output,
        mutable_edge_ids=frozenset({"inside"}), mutable_junction_ids=frozenset({"b"}))
    assert report["status"] == "blocked"
    assert report["fixed_boundary_failures"][0]["end"] == "to"


def test_unknown_scope_and_overwriting_are_rejected(tmp_path):
    source = _network(tmp_path)
    with pytest.raises(ValueError, match="new input"):
        prepare_geometry_frozen_input(source_file=source, output_file=source)
    with pytest.raises(ValueError, match="unknown external"):
        prepare_geometry_frozen_input(source_file=source, output_file=tmp_path / "new.net.xml",
                                      mutable_edge_ids=frozenset({"not_present"}))


def test_node_position_changes_are_not_hidden_by_an_unchanged_polygon(tmp_path):
    source = _network(tmp_path)
    output = tmp_path / "candidate.net.xml"
    prepare_geometry_frozen_input(source_file=source, output_file=output)
    tree = ET.parse(output)
    tree.getroot().find("junction").set("x", "50")
    tree.write(output, encoding="utf-8")
    result = compare_frozen_geometry(source_file=source, candidate_file=output)
    assert result["status"] == "blocked"
    assert result["outside_scope_geometry"]["junctions"]["changed"] == ["a"]


def test_scoped_admission_preserves_the_remote_road_end(tmp_path):
    from astra_sumo.core.junction_rebuild.restoration import restore_off_scope_netconvert_artifacts

    source = _network(tmp_path)
    candidate = tmp_path / "proposal.net.xml"
    candidate.write_bytes(source.read_bytes())
    tree = ET.parse(candidate)
    tree.getroot().findall("edge")[1].find("lane").set("shape", "11,0 25,0")
    tree.getroot().findall("edge")[1].find("lane").set("allow", "bus")
    tree.write(candidate, encoding="utf-8")
    scope = {"mutable_edge_ids": frozenset({"inside"}), "mutable_junction_ids": frozenset({"b"})}
    assert compare_frozen_geometry(source_file=source, candidate_file=candidate, **scope)["status"] == "blocked"
    admission = restore_off_scope_netconvert_artifacts(source_file=source, target_file=candidate,
        **scope, expand_mutable_edge_endpoints=False)
    assert admission["status"] == "pass"
    assert compare_frozen_geometry(source_file=source, candidate_file=candidate, **scope)["status"] == "pass"
    assert ET.parse(candidate).getroot().findall("edge")[1].find("lane").get("shape").split()[-1] == "20,0"
    assert ET.parse(candidate).getroot().findall("edge")[1].find("lane").get("allow") == "bus"


def test_boundary_admission_keeps_source_endpoint_precision(tmp_path):
    from astra_sumo.core.junction_rebuild.restoration import restore_off_scope_netconvert_artifacts

    source = _network(tmp_path)
    tree = ET.parse(source)
    tree.getroot().findall("edge")[1].find("lane").set("shape", "10,0 20.123456789,0")
    tree.write(source, encoding="utf-8")
    candidate = tmp_path / "proposal.net.xml"
    tree.getroot().findall("edge")[1].find("lane").set("shape", "11,0 25,0")
    tree.write(candidate, encoding="utf-8")
    scope = {"mutable_edge_ids": {"inside"}, "mutable_junction_ids": {"b"}}
    result = restore_off_scope_netconvert_artifacts(source_file=source, target_file=candidate,
        **scope, expand_mutable_edge_endpoints=False)
    assert result["status"] == "pass"
    assert compare_frozen_geometry(source_file=source, candidate_file=candidate, **scope)["status"] == "pass"


def test_boundary_admission_rejects_missing_source_geometry(tmp_path):
    from astra_sumo.core.junction_rebuild.restoration import restore_off_scope_netconvert_artifacts

    source = _network(tmp_path)
    tree = ET.parse(source)
    tree.getroot().findall("edge")[1].find("lane").attrib.pop("shape")
    tree.write(source, encoding="utf-8")
    candidate = tmp_path / "proposal.net.xml"
    candidate.write_bytes(source.read_bytes())
    result = restore_off_scope_netconvert_artifacts(source_file=source, target_file=candidate,
        mutable_edge_ids={"inside"}, mutable_junction_ids={"b"}, expand_mutable_edge_endpoints=False)
    assert result["status"] == "fail"
    assert any(row["reason"] == "fixed_boundary_geometry_not_preserved" for row in result["failures"])


def test_added_local_lane_uses_explicit_split_road_boundary_correspondence(tmp_path):
    from astra_sumo.core.junction_rebuild.restoration import restore_off_scope_netconvert_artifacts

    source = _network(tmp_path)
    before = source.read_bytes()
    tree = ET.parse(source)
    root = tree.getroot()
    edge = root.find("edge[@id='inside']")
    edge.set("from", "split")
    edge.find("lane").set("shape", "15,0 20,0")
    ET.SubElement(edge, "lane", id="inside_1", index="1", length="5", shape="15,0 20,1")
    upstream = ET.SubElement(root, "edge", id="inside.before", **{"from": "b", "to": "split"})
    ET.SubElement(upstream, "lane", id="inside.before_0", index="0", length="4", shape="11,0 15,0")
    ET.SubElement(root, "junction", id="split", shape="14,-1 14,1 16,1")
    candidate = tmp_path / "proposal.net.xml"
    tree.write(candidate, encoding="utf-8")
    scope = dict(mutable_edge_ids={"inside", "inside.before"}, mutable_junction_ids={"split", "c"},
                 boundary_edge_map={"inside": "inside.before"})
    assert compare_frozen_geometry(source_file=source, candidate_file=candidate, **scope)["status"] == "blocked"
    result = restore_off_scope_netconvert_artifacts(source_file=source, target_file=candidate,
        **scope, expand_mutable_edge_endpoints=False)
    assert result["status"] == "pass"
    assert compare_frozen_geometry(source_file=source, candidate_file=candidate, **scope)["status"] == "pass"
    assert len(ET.parse(candidate).getroot().find("edge[@id='inside']").findall("lane")) == 2
    assert source.read_bytes() == before


def test_scoped_admission_rejects_a_new_lane_at_a_fixed_boundary(tmp_path):
    from astra_sumo.core.junction_rebuild.restoration import restore_off_scope_netconvert_artifacts

    source = _network(tmp_path)
    candidate = tmp_path / "proposal.net.xml"
    tree = ET.parse(source)
    ET.SubElement(tree.getroot().findall("edge")[1], "lane", id="inside_1", index="1", length="10", shape="10,1 20,1")
    tree.write(candidate, encoding="utf-8")
    report = restore_off_scope_netconvert_artifacts(source_file=source, target_file=candidate,
        mutable_edge_ids={"inside"}, mutable_junction_ids={"b"}, expand_mutable_edge_endpoints=False)
    assert report["status"] == "fail"
    assert any(failure["reason"] == "boundary_lane_identity_changed" for failure in report["failures"])
