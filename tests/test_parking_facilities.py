from __future__ import annotations

import json
from pathlib import Path
import xml.etree.ElementTree as ET

from PIL import Image
import pytest

from astra_sumo.cli import main
from astra_sumo.core.candidate_contracts import file_sha256
from astra_sumo.core.corridor_edit_ledger import validate_edit_ledger, _is_structurally_protected_edge, propose_corridor_edits
from astra_sumo.core.network_aerial_review import render_network_aerial_review
from astra_sumo.core.network_edit_operations import validate_scoped_operation
from astra_sumo.core.parking_facilities import build_parking_facility_model, identify_parking_facilities


def _fixture(tmp_path: Path):
    osm = tmp_path / "input.osm.xml"
    osm.write_text('''<osm version="0.6">
<node id="1" lon="9" lat="52"/><node id="2" lon="9.001" lat="52"/>
<node id="3" lon="9.001" lat="52.001"/><node id="4" lon="9" lat="52.001"/>
<node id="5" lon="9.0003" lat="52.0003"/><node id="6" lon="9.0007" lat="52.0007"/>
<way id="100"><nd ref="1"/><nd ref="2"/><nd ref="3"/><nd ref="4"/><nd ref="1"/>
<tag k="building" v="parking"/><tag k="name" v="Test Garage"/></way>
<way id="200"><nd ref="5"/><nd ref="6"/><tag k="highway" v="service"/>
<tag k="tunnel" v="building_passage"/></way></osm>''', encoding="utf-8")
    network = tmp_path / "input.net.xml"
    network.write_text('''<net><location netOffset="0,0" projParameter="EPSG:32632"/>
<edge id="entry" from="A" to="I"><lane id="entry_0" index="0" shape="500000,5761060 500020,5761080"/></edge>
<edge id="inside" from="I" to="J"><lane id="inside_0" index="0" shape="500020,5761080 500050,5761120">
<param key="origId" value="200"/></lane><param key="tunnel" value="building_passage"/></edge>
<edge id="exit" from="J" to="B"><lane id="exit_0" index="0" shape="500050,5761120 500070,5761140"/></edge>
<edge id=":I_0" function="internal"><lane id=":I_0_0" index="0" shape="500020,5761080 500021,5761081"/></edge>
<edge id=":J_0" function="internal"><lane id=":J_0_0" index="0" shape="500049,5761119 500050,5761120"/></edge>
<junction id="I" x="500020" y="5761080" shape="500018,5761078 500022,5761078 500022,5761082"/>
<junction id="J" x="500050" y="5761120"/>
<connection from="entry" to="inside" fromLane="0" toLane="0" via=":I_0_0" dir="s"/>
<connection from=":I_0" to="inside" fromLane="0" toLane="0"/>
<connection from="inside" to="exit" fromLane="0" toLane="0" via=":J_0_0" dir="s"/>
<connection from=":J_0" to="exit" fromLane="0" toLane="0"/>
</net>''', encoding="utf-8")
    return network, osm


def _build(network, osm, output):
    return build_parking_facility_model(net_file=network, osm_file=osm, output_dir=output,
        expected_net_sha256=file_sha256(network), expected_osm_sha256=file_sha256(osm))


def _runtime(element):
    return (element.tag, sorted(element.attrib.items()), [_runtime(child) for child in element
            if not (child.tag == "param" and child.get("key", "").startswith("astra.parking."))])


def test_facility_annotation_preserves_full_runtime_network_and_inputs(tmp_path: Path) -> None:
    network, osm = _fixture(tmp_path)
    before = (file_sha256(network), file_sha256(osm))
    report = _build(network, osm, tmp_path / "first")
    second = _build(network, osm, tmp_path / "second")
    assert _runtime(ET.parse(network).getroot()) == _runtime(ET.parse(report["network_file"]).getroot())
    assert report["network"]["sha256"] == second["network"]["sha256"]
    assert Path(report["report_file"]).read_bytes() == Path(second["report_file"]).read_bytes()
    assert before == (file_sha256(network), file_sha256(osm))
    facility = report["facilities"][0]
    assert facility["interior_edge_ids"] == ["inside"]
    assert facility["internal_lane_ids"] == [":I_0_0", ":J_0_0"]
    assert [(p["role"], p["edge_id"]) for p in facility["portals"]] == [("entry", "entry"), ("exit", "exit")]
    assert facility["capacity"] is None and facility["parking_behavior"] == "not_configured"
    assert report["runtime_geometry_changed"] is False
    assert report["promotion_authority"] is False
    assert all(_is_structurally_protected_edge(edge) for edge in ET.parse(report["network_file"]).getroot().findall("edge"))


def test_mixed_origins_are_not_collapsed_into_a_parking_building(tmp_path: Path) -> None:
    network, osm = _fixture(tmp_path)
    network.write_text(network.read_text().replace('value="200"', 'value="200 999"'), encoding="utf-8")
    report = identify_parking_facilities(net_file=network, osm_file=osm)
    assert report["facilities"] == []
    assert report["unresolved"] == [{"id": "inside", "reason": "mixed_interior_and_exterior_origins"}]


def test_a_nonparking_building_is_not_inferred_to_be_a_garage(tmp_path: Path) -> None:
    network, osm = _fixture(tmp_path)
    osm.write_text(osm.read_text().replace('k="building" v="parking"', 'k="building" v="warehouse"'), encoding="utf-8")
    assert identify_parking_facilities(net_file=network, osm_file=osm)["facilities"] == []


def test_parking_aisle_does_not_need_an_extra_tunnel_tag(tmp_path: Path) -> None:
    network, osm = _fixture(tmp_path)
    osm.write_text(osm.read_text().replace('k="tunnel" v="building_passage"', 'k="service" v="parking_aisle"'), encoding="utf-8")
    root = ET.parse(network).getroot()
    inside = next(e for e in root.findall("edge") if e.get("id") == "inside")
    inside.remove(inside.find("param"))
    ET.ElementTree(root).write(network)
    report = identify_parking_facilities(net_file=network, osm_file=osm)
    assert report["facilities"][0]["internal_lane_ids"] == [":I_0_0", ":J_0_0"]
    assert len(report["facilities"][0]["portals"]) == 2


def test_incomplete_footprint_and_relation_require_review(tmp_path: Path) -> None:
    network, osm = _fixture(tmp_path)
    root = ET.parse(osm).getroot()
    way = next(w for w in root.findall("way") if w.get("id") == "100")
    way.remove(way.findall("nd")[-1])
    relation = ET.SubElement(root, "relation", id="300")
    ET.SubElement(relation, "tag", k="building", v="parking")
    ET.ElementTree(root).write(osm)
    report = identify_parking_facilities(net_file=network, osm_file=osm)
    assert report["facilities"] == []
    assert len(report["unresolved"]) == 2


def test_relation_outer_way_is_not_used_without_its_inner_rings(tmp_path: Path) -> None:
    network, osm = _fixture(tmp_path)
    root = ET.parse(osm).getroot()
    relation = ET.SubElement(root, "relation", id="300")
    ET.SubElement(relation, "tag", k="building", v="parking")
    ET.SubElement(relation, "member", type="way", ref="100", role="outer")
    ET.ElementTree(root).write(osm)
    result = identify_parking_facilities(net_file=network, osm_file=osm)
    assert result["facilities"] == []
    assert result["supported_footprint_count"] == 0
    assert result["unresolved"][0]["member_way_ids"] == ["100"]


def test_duplicate_parking_aisles_are_not_automatic_deletion_candidates(tmp_path: Path) -> None:
    path = tmp_path / "parking.net.xml"
    path.write_text('''<net><edge id="a" from="x" to="y"><param key="service" value="parking_aisle"/>
<lane id="a_0" shape="0,0 10,0"/></edge><edge id="b" from="x" to="y">
<param key="service" value="parking_aisle"/><lane id="b_0" shape="0,0 10,0"/></edge></net>''', encoding="utf-8")
    assert propose_corridor_edits(path, include_micro_merges=False) == []


def test_junction_join_cannot_absorb_a_parking_interior(tmp_path: Path) -> None:
    network, _osm = _fixture(tmp_path)
    operation = {"operation": "join_junctions", "target_ids": [],
                 "params": {"node_ids": ["I", "J"], "join_id": "joined"}}
    errors = validate_scoped_operation(operation, ET.parse(network).getroot())
    assert errors and "parking access" in str(errors)


def test_hash_and_fresh_output_guards(tmp_path: Path) -> None:
    network, osm = _fixture(tmp_path)
    with pytest.raises(ValueError, match="hashes"):
        build_parking_facility_model(net_file=network, osm_file=osm, output_dir=tmp_path / "bad",
            expected_net_sha256="0" * 64, expected_osm_sha256=file_sha256(osm))
    assert not (tmp_path / "bad").exists()
    _build(network, osm, tmp_path / "good")
    with pytest.raises(ValueError, match="new parking"):
        _build(network, osm, tmp_path / "good")


@pytest.mark.parametrize("params", ['<param key="tunnel" value="building_passage"/>',
    '<param key="service" value="parking_aisle"/>', '<param key="astra.parking.facility" value="parking-1"/>'])
def test_generic_deletion_cannot_remove_a_protected_facility(params: str) -> None:
    root = ET.fromstring(f'<net><edge id="road" from="a" to="b">{params}<lane id="road_0"/></edge></net>')
    assert _is_structurally_protected_edge(root.find("edge"))
    operation = {"id": "delete", "operation": "delete_edge", "status": "accepted", "target_ids": ["road"],
                 "evidence": ["old review"], "rationale": "old deletion", "rollback": {"action": "restore"}}
    result = validate_edit_ledger({"operations": [operation]}, root=root)
    assert result["status"] == "fail"
    assert any(row["code"] == "protected_target_requires_explicit_review" for row in result["errors"])


def test_facility_overview_and_full_detail_are_both_available(tmp_path: Path) -> None:
    network, osm = _fixture(tmp_path)
    model = _build(network, osm, tmp_path / "model")
    aerial = tmp_path / "aerial.png"
    Image.new("RGB", (600, 600), (160, 170, 180)).save(aerial)
    request = {"schema": "astra.network-aerial-review-request/v1", "target_date": None,
        "candidate_net": {"path": model["network_file"], "sha256": model["network"]["sha256"]},
        "parking_facilities": {"path": model["report_file"], "sha256": file_sha256(Path(model["report_file"]))},
        "aerial": {"path": str(aerial), "sha256": file_sha256(aerial), "crs": "EPSG:32632",
            "bbox": [499990, 5761040, 500100, 5761160], "north_up": True, "capture_date": None,
            "authority": "Synthetic", "license": "MIT", "source_url": "synthetic://test"},
        "views": [{"id": "parking", "bbox": [499990, 5761040, 500100, 5761160]}], "width_px": 600}
    path = tmp_path / "review.json"
    path.write_text(json.dumps(request), encoding="utf-8")
    report = render_network_aerial_review(request_file=path, output_dir=tmp_path / "review")
    assert report["parking_facility_ids"] == ["osm-way-100"]
    assert report["artifacts"]["parking-candidate_net.png"] != report["artifacts"]["parking-candidate_net-structures.png"]
    assert "inside_0" in report["views"][0]["visible_objects"]["candidate_net"]["lanes"]
    other_network = tmp_path / "unbound.net.xml"
    other_network.write_bytes(network.read_bytes() + b"\n")
    request["candidate_net"]["sha256"] = file_sha256(other_network)
    request["candidate_net"]["path"] = str(other_network)
    path.write_text(json.dumps(request), encoding="utf-8")
    with pytest.raises(ValueError, match="another network"):
        render_network_aerial_review(request_file=path, output_dir=tmp_path / "wrong")
    assert not (tmp_path / "wrong").exists()


def test_cli_builds_parking_model(tmp_path: Path, capsys) -> None:
    network, osm = _fixture(tmp_path)
    code = main(["network", "parking-facilities", str(network), str(osm), str(tmp_path / "model"),
                 "--expected-net-sha256", file_sha256(network), "--expected-osm-sha256", file_sha256(osm), "--json"])
    assert code == 1
    assert json.loads(capsys.readouterr().out)["annotation_status"] == "pass"
