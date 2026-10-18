from __future__ import annotations

from pathlib import Path
import json
import shutil
import xml.etree.ElementTree as ET

import pytest

from astra_sumo.core.osm_access import correct_osm_access_permissions, _patch_lane_permissions


def _inputs(tmp_path, *, widen=False):
    osm = tmp_path / "source.osm.xml"
    osm.write_text('''<osm><way id="7"><tag k="vehicle" v="no"/>
        <tag k="bus" v="yes"/><tag k="foot" v="''' + ("yes" if widen else "no") + '''"/></way></osm>''', encoding="utf-8")
    net = tmp_path / "source.net.xml"
    net.write_bytes(b'''<?xml version="1.0" encoding="UTF-8"?>
<!-- Keep this provenance comment byte-for-byte. -->
<net><edge id="road" from="a" to="b"><lane id="road_0" index="0" allow="passenger bus"
 length="10.00" speed="10" shape="0,0 10,0"><param key="origId" value="7"/></lane></edge>
<edge id="outside" from="b" to="c"><lane id="outside_0" index="0" allow="passenger"
 length="10.00" speed="10" shape="11,0 21,0"/></edge>
<edge id=":b_0" function="internal"><lane id=":b_0_0" index="0" length="1" speed="5" shape="10,0 11,0"/></edge>
<junction id="b" type="priority" x="10.5" y="0" incLanes="road_0" intLanes=":b_0_0" shape="10,-1 11,-1 11,1">
 <request index="0" response="0" foes="0" cont="0"/></junction>
<connection from="road" fromLane="0" to="outside" toLane="0" via=":b_0_0" dir="s" state="M"/>
<tlLogic id="unrelated" type="static" programID="0" offset="0"><phase duration="30" state="G"/></tlLogic></net>''')
    return osm, net


def _load_ok(command, **kwargs):
    assert Path(command[0]).stem == "sumo"
    assert "--sumo-net-file" not in command
    assert "-n" in command
    return {"status": "pass", "returncode": 0}


def test_permission_edit_preserves_every_other_parsed_object_and_original_bytes(tmp_path):
    osm, net = _inputs(tmp_path)
    original, original_osm = net.read_bytes(), osm.read_bytes()
    output = tmp_path / "edit"
    report = correct_osm_access_permissions(osm_file=osm, net_file=net, output_dir=output, command_runner=_load_ok)
    assert report["status"] == "pass"
    assert report["checks"]["non_permission_network_state_preserved"]
    assert report["netconvert_rebuild_count"] == 0
    assert net.read_bytes() == original.replace(b' allow="passenger bus"', b' allow="bus"')
    assert (output / "native-import.net.xml").read_bytes() == original
    assert osm.read_bytes() == original_osm


@pytest.mark.parametrize("failure", ["source_load_failure", "candidate_load_failure", "candidate_mutation", "source_mutation"])
def test_failed_validation_never_replaces_the_import(tmp_path, failure):
    osm, net = _inputs(tmp_path)
    original = net.read_bytes()

    def rejected(command, **kwargs):
        candidate = Path(command[command.index("-n") + 1])
        if failure == "source_load_failure" or (failure == "candidate_load_failure" and candidate.name == "access-corrected.net.xml"):
            return {"status": "fail", "returncode": 1}
        if failure == "candidate_mutation" and candidate.name == "access-corrected.net.xml":
            candidate.write_bytes(candidate.read_bytes().replace(b'length="10.00"', b'length="20.00"'))
        elif failure == "source_mutation":
            osm.write_text("<osm/>", encoding="utf-8")
        return {"status": "pass", "returncode": 0}

    report = correct_osm_access_permissions(osm_file=osm, net_file=net, output_dir=tmp_path / "edit", command_runner=rejected)
    assert report["status"] == "blocked"
    assert net.read_bytes() == original
    if failure == "source_load_failure":
        assert report["blocked_reason"] == "input_network_failed_sumo_load"
        assert not (tmp_path / "edit/access-corrected.net.xml").exists()


def test_new_modes_keep_a_separate_connectivity_review(tmp_path):
    osm, net = _inputs(tmp_path, widen=True)
    report = correct_osm_access_permissions(osm_file=osm, net_file=net, output_dir=tmp_path / "edit", command_runner=_load_ok)
    assert report["status"] == "review_required"
    assert report["connectivity_review_required"] is True
    assert report["checks"]["non_permission_network_state_preserved"] is True
    assert any(row.get("added_vehicle_classes") == ["pedestrian"] for row in report["review"])


def test_patch_handles_non_self_closing_lanes_quotes_and_escaped_names():
    raw = b'''<net><edge id="a"><lane id='a_0' type="a&gt;b" allow='passenger'>
      <param key="note" value="unrelated"/></lane></edge></net>'''
    result = _patch_lane_permissions(raw, ET.fromstring(raw), {"a_0": {"allow": "bus"}})
    assert result == raw.replace(b" allow='passenger'", b' allow="bus"')


def test_comment_that_imitates_a_lane_tag_is_rejected(tmp_path):
    osm, net = _inputs(tmp_path)
    original = net.read_bytes().replace(b"<net>", b'<!-- <lane id="fake"/> --><net>')
    net.write_bytes(original)
    with pytest.raises(ValueError, match="opening tags"):
        correct_osm_access_permissions(osm_file=osm, net_file=net, output_dir=tmp_path / "edit", command_runner=_load_ok)
    assert net.read_bytes() == original


def test_import_keeps_new_mode_connectivity_as_review_required(tmp_path, monkeypatch):
    from astra_sumo.core import osm_access
    from astra_sumo.core.osm_network import build_osm_network

    binary = shutil.which("netconvert")
    if not binary:
        pytest.skip("Native netconvert is required.")
    monkeypatch.setattr(osm_access, "correct_osm_access_permissions", lambda **kwargs: {
        "status": "review_required", "connectivity_review_required": True})
    result = build_osm_network(bbox="9.985,53.554,9.993,53.560", output_dir=tmp_path / "build",
        source_osm_path=Path(__file__).parent / "fixtures/osm_bus_access_118.osm.xml",
        allowed_highways={"service"}, clip_source_ways_to_bbox=False, netconvert_binary=binary)
    assert result["status"] == "review_required"
    assert result["claim_status"] == "diagnostic-demo"
    manifest = json.loads(Path(result["build_manifest_file"]).read_text(encoding="utf-8"))
    assert manifest["status"] == "review_required"


@pytest.mark.skipif(not shutil.which("netconvert"), reason="Native netconvert is required.")
def test_import_preserves_tls_cleanup_artifacts_before_permission_edits(tmp_path):
    from astra_sumo.core.candidate_contracts import file_sha256
    from astra_sumo.core.osm_network import build_osm_network

    result = build_osm_network(bbox="9.985,53.554,9.993,53.560", output_dir=tmp_path / "build",
        source_osm_path=Path(__file__).parent / "fixtures/osm_bus_access_118.osm.xml",
        allowed_highways={"service"}, clip_source_ways_to_bbox=False, netconvert_binary=shutil.which("netconvert"))
    assert result["status"] in {"pass", "review_required"}
    cleanup = result["tls_reference_cleanup"]
    assert cleanup["status"] == "pass"
    assert file_sha256(Path(cleanup["source_net_file"])) == cleanup["source_sha256"]
    assert file_sha256(Path(cleanup["candidate_net_file"])) == cleanup["candidate_sha256"]
    assert Path(result["net_file"]) != Path(cleanup["candidate_net_file"])


@pytest.mark.skipif(not shutil.which("netconvert"), reason="Native netconvert is required.")
@pytest.mark.parametrize("precision,expected", [(None, 6), (2, 2), (6, 6)])
def test_product_import_keeps_precision_in_native_geometry_and_recipe(tmp_path, precision, expected):
    from astra_sumo.core.candidate_contracts import file_sha256
    from astra_sumo.core.osm_network import build_osm_network

    source = Path(__file__).parent / "fixtures/osm_bus_access_118.osm.xml"
    before = file_sha256(source)
    result = build_osm_network(bbox="9.985,53.554,9.993,53.560", output_dir=tmp_path / "build",
        source_osm_path=source, allowed_highways={"service"}, clip_source_ways_to_bbox=False,
        netconvert_binary=shutil.which("netconvert"), output_precision=precision)
    assert result["status"] == "pass", result
    manifest = json.loads(Path(result["build_manifest_file"]).read_text(encoding="utf-8"))
    command = manifest["netconvert"]["command"]
    assert command[command.index("--precision") + 1] == str(expected)
    assert manifest["netconvert"]["precision"] == result["netconvert_precision"] == expected
    assert "--precision " + str(expected) in Path(result["command_record"]).read_text(encoding="utf-8")
    coordinates = [float(value) for edge in ET.parse(result["net_file"]).getroot().findall("edge")
        if not edge.get("function") for lane in edge.findall("lane")
        for point in lane.get("shape").split() for value in point.split(",")]
    assert any(abs(value - round(value, 2)) > 1e-7 for value in coordinates) is (expected == 6)
    assert file_sha256(source) == before


@pytest.mark.parametrize("precision", [True, -1, 2.5, "6"])
def test_invalid_import_precision_returns_an_input_error_before_writing(tmp_path, precision):
    from astra_sumo.core.osm_network import build_osm_network
    destination = tmp_path / "build"
    report = build_osm_network(bbox="9.985,53.554,9.993,53.560", output_dir=destination, output_precision=precision)
    assert report["status"] == "fail" and "output_precision" in report["error"]
    assert not destination.exists()
