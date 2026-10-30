from __future__ import annotations

from pathlib import Path
import json
import re
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


def test_new_mode_without_a_loaded_movement_is_reverted(tmp_path):
    osm, net = _inputs(tmp_path, widen=True)
    original = net.read_bytes()
    report = correct_osm_access_permissions(osm_file=osm, net_file=net, output_dir=tmp_path / "edit", command_runner=_load_ok)
    assert report["status"] == "pass"
    assert report["connectivity_review_required"] is False
    assert report["checks"]["non_permission_network_state_preserved"] is True
    [row] = report["added_class_movements"]["rows"]
    assert row == {"lane_id": "road_0", "vehicle_class": "pedestrian", "movement": "no_neighbour_allows_class",
                   "status": "no_effect", "reason": "new_class_has_no_loaded_movement"}
    assert report["changed_lanes"][0]["after"] == {"allow": "bus"}
    assert report["changed_lanes"][0]["reverted_vehicle_classes"] == ["pedestrian"]
    assert net.read_bytes() == original.replace(b' allow="passenger bus"', b' allow="bus"')
    assert b'allow="bus"' in Path(report["edge_patch"]["path"]).read_bytes()


_NET_TAIL = b'''<edge id="outside" from="b" to="c"><lane id="outside_0" index="0" allow="passenger taxi"
 length="10.00" speed="10" shape="11,0 21,0"/></edge>
<junction id="b" type="priority" x="10.5" y="0" incLanes="road_0" intLanes=":b_0_0" shape="10,-1 11,-1 11,1">
 <request index="0" response="0" foes="0" cont="0"/></junction>
<connection from="road" fromLane="0" to="outside" toLane="0" via=":b_0_0" dir="s" state="M"/>
<connection from=":b_0" fromLane="0" to="outside" toLane="0" dir="s" state="M"/></net>'''


def _service_inputs(tmp_path, *, tags, road_lanes, via_allow=None):
    osm = tmp_path / "source.osm.xml"
    osm.write_text('<osm><way id="7">' + "".join(f'<tag k="{key}" v="{value}"/>' for key, value in tags.items())
                   + '</way><way id="8"><tag k="highway" v="footway"/><tag k="bicycle" v="yes"/><tag k="access" v="private"/>'
                   '<tag k="bicycle:conditional" v="no @ (22:00-06:00)"/></way></osm>', encoding="utf-8")
    lanes = "".join(f'<lane id="road_{index}" index="{index}" {permission} length="10.00" speed="10" shape="0,{index} 10,{index}">'
                    '<param key="origId" value="7"/></lane>' for index, permission in enumerate(road_lanes))
    via = f' allow="{via_allow}"' if via_allow else ""
    net = tmp_path / "source.net.xml"
    net.write_bytes(b'<?xml version="1.0" encoding="UTF-8"?>\n<net version="1.20">'
                    + f'<edge id=":b_0" function="internal"><lane id=":b_0_0" index="0"{via} length="1" speed="5" shape="10,0 11,0"/></edge>'
                    '<edge id="road" from="a" to="b" type="highway.service">'.encode() + lanes.encode() + b"</edge>\n" + _NET_TAIL)
    return osm, net


@pytest.mark.parametrize("via_allow,movement,after", [
    (None, "usable", {"bicycle", "pedestrian", "taxi"}),
    ("delivery bicycle", "internal_lane_blocks_class", {"bicycle", "pedestrian"}),
])
def test_added_class_is_kept_only_with_a_loaded_movement(tmp_path, via_allow, movement, after):
    from astra_sumo.core.osm_access import _permission_set
    osm, net = _service_inputs(tmp_path, tags={"highway": "service", "motor_vehicle": "no", "taxi": "yes"},
                               road_lanes=['allow="pedestrian delivery bicycle"'], via_allow=via_allow)
    report = correct_osm_access_permissions(osm_file=osm, net_file=net, output_dir=tmp_path / "edit", command_runner=_load_ok)
    assert report["status"] == "pass" and report["connectivity_review_required"] is False
    assert [row["movement"] for row in report["added_class_movements"]["rows"]] == [movement]
    assert report["added_class_movements"]["reverted_count"] == (movement != "usable")
    lane = ET.parse(net).getroot().find("edge[@id='road']/lane")
    assert _permission_set(lane.attrib, network_version="1.20") == after
    assert report["checks"]["source_permissions_applied"] and report["checks"]["other_lane_permissions_preserved"]


def test_reverting_every_widening_leaves_the_network_unchanged(tmp_path):
    osm, net = _service_inputs(tmp_path, tags={"highway": "service", "vehicle": "no", "bus": "yes", "foot": "yes"},
                               road_lanes=['allow="bus"'])
    original = net.read_bytes()
    report = correct_osm_access_permissions(osm_file=osm, net_file=net, output_dir=tmp_path / "edit", command_runner=_load_ok)
    assert report["status"] == "pass"
    assert report["changed_lane_count"] == 0 and report["changed_lanes"] == []
    assert report["added_class_movements"]["reverted_by_class"] == {"pedestrian": 1}
    assert net.read_bytes() == original


@pytest.mark.parametrize("tags", [{"highway": "service", "vehicle": "no", "bicycle": "yes", "foot": "yes"},
                                  {"highway": "service", "access": "no", "vehicle": "no", "bus": "yes"}])
def test_dedicated_sidewalk_lane_keeps_its_permission(tmp_path, tags):
    osm, net = _service_inputs(tmp_path, tags=tags, road_lanes=['allow="pedestrian"', 'allow="passenger delivery bus"'])
    report = correct_osm_access_permissions(osm_file=osm, net_file=net, output_dir=tmp_path / "edit", command_runner=_load_ok)
    assert report["status"] == "pass", report["review"]
    assert report["dedicated_lanes_preserved"] == 1
    lanes = {lane.get("id"): lane.attrib for lane in ET.parse(net).getroot().findall("edge[@id='road']/lane")}
    assert lanes["road_0"]["allow"] == "pedestrian"
    assert "pedestrian" not in lanes["road_1"].get("allow", "").split()
    assert [row["lane_id"] for row in report["changed_lanes"]] == ["road_1"]
    assert any(row["reason"] == "mode_kept_for_dedicated_lane" for way in report["decisions"] for row in way["decisions"])


def test_access_tags_outside_the_gate_are_counted_not_evaluated(tmp_path):
    osm, net = _service_inputs(tmp_path, tags={"highway": "service", "motor_vehicle": "no", "taxi": "yes"},
                               road_lanes=['allow="pedestrian delivery bicycle"'])
    report = correct_osm_access_permissions(osm_file=osm, net_file=net, output_dir=tmp_path / "edit", command_runner=_load_ok)
    assert report["unevaluated_outside_gate"] == {"way_count": 1, "by_key": {"access": 1, "bicycle": 1, "bicycle:conditional": 1},
                                                  "by_value": {"access=private": 1, "bicycle=yes": 1}}


_MOVEMENT_NET = '''<?xml version="1.0" encoding="UTF-8"?>
<net version="1.20"{header}>
<edge id=":j_0" function="internal"><lane id=":j_0_0" index="0" allow="passenger" length="1" speed="5" shape="0,0 1,0"/></edge>
<edge id=":j_1" function="internal"><lane id=":j_1_0" index="0" length="1" speed="5" shape="0,0 1,0"/></edge>
<edge id=":j_2" function="internal"><lane id=":j_2_0" index="0" length="1" speed="5" shape="0,0 1,0"/></edge>
<edge id=":j_3" function="internal"><lane id=":j_3_0" index="0" allow="passenger" length="1" speed="5" shape="0,0 1,0"/></edge>
{walkingarea}
<edge id="usable" from="u" to="j"><lane id="usable_0" index="0" allow="passenger taxi" length="5" speed="5" shape="0,0 5,0"/></edge>
<edge id="blocked" from="v" to="j"><lane id="blocked_0" index="0" allow="passenger taxi" length="5" speed="5" shape="0,0 5,0"/></edge>
<edge id="chain" from="w" to="j"><lane id="chain_0" index="0" allow="passenger taxi" length="5" speed="5" shape="0,0 5,0"/></edge>
<edge id="busless" from="x" to="j"><lane id="busless_0" index="0" allow="passenger bus" length="5" speed="5" shape="0,0 5,0"/></edge>
<edge id="footway" from="y" to="z"><lane id="footway_0" index="0" allow="pedestrian bicycle" length="5" speed="5" shape="0,0 5,0"/></edge>
<edge id="out" from="j" to="k"><lane id="out_0" index="0" allow="passenger taxi" length="5" speed="5" shape="0,0 5,0"/></edge>
<edge id="walk" from="j" to="m"><lane id="walk_0" index="0" allow="pedestrian" length="5" speed="5" shape="0,0 5,0"/></edge>
<edge id="road" from="n" to="j"><lane id="road_0" index="0" allow="passenger pedestrian" length="5" speed="5" shape="0,0 5,0"/></edge>
<edge id="alone" from="p" to="q"><lane id="alone_0" index="0" allow="passenger pedestrian" length="5" speed="5" shape="0,0 5,0"/></edge>
<junction id="j" type="priority" x="0" y="0" incLanes="usable_0" intLanes=":j_0_0" shape="0,0 1,0 1,1"/>
<connection from="usable" fromLane="0" to="out" toLane="0" via=":j_1_0" dir="s" state="M"/>
<connection from="blocked" fromLane="0" to="out" toLane="0" via=":j_0_0" dir="s" state="M"/>
<connection from="chain" fromLane="0" to="out" toLane="0" via=":j_2_0" dir="s" state="M"/>
<connection from="busless" fromLane="0" to="out" toLane="0" via=":j_1_0" dir="s" state="M"/>
<connection from=":j_0" fromLane="0" to="out" toLane="0" dir="s" state="M"/>
<connection from=":j_1" fromLane="0" to="out" toLane="0" dir="s" state="M"/>
<connection from=":j_2" fromLane="0" to="out" toLane="0" via=":j_3_0" dir="s" state="M"/>
<connection from=":j_3" fromLane="0" to="out" toLane="0" dir="s" state="M"/>
{walkingarea_connection}
</net>'''
_ADDITIONS = {"usable_0": {"taxi"}, "blocked_0": {"taxi"}, "chain_0": {"taxi"}, "busless_0": {"bus"},
              "footway_0": {"bicycle"}, "road_0": {"pedestrian"}, "alone_0": {"pedestrian"}}


def test_movement_check_reads_connections_and_every_internal_lane(tmp_path):
    from astra_sumo.core.osm_access import measure_added_class_movements
    net = tmp_path / "movements.net.xml"
    net.write_text(_MOVEMENT_NET.format(header="", walkingarea="", walkingarea_connection=""), encoding="utf-8")
    result = measure_added_class_movements(net, _ADDITIONS)
    assert result["network_has_walkingareas"] is False
    assert result["additions"] == {
        "usable_0": {"taxi": "usable"}, "blocked_0": {"taxi": "internal_lane_blocks_class"},
        "chain_0": {"taxi": "internal_lane_blocks_class"}, "busless_0": {"bus": "no_neighbour_allows_class"},
        "footway_0": {"bicycle": "no_loaded_movement"},
        # Without walking areas, SUMO pedestrians change edges at a junction without connections.
        "road_0": {"pedestrian": "usable"}, "alone_0": {"pedestrian": "no_neighbour_allows_class"}}


def test_pedestrians_need_a_walkingarea_connection_in_a_walkingarea_network(tmp_path):
    from astra_sumo.core.osm_access import measure_added_class_movements
    net = tmp_path / "walkingareas.net.xml"
    net.write_text(_MOVEMENT_NET.format(
        header=' walkingareas="true"',
        walkingarea='<edge id=":j_w0" function="walkingarea"><lane id=":j_w0_0" index="0" allow="pedestrian" length="1" speed="1" shape="0,0 1,0"/></edge>',
        walkingarea_connection='<connection from="road" fromLane="0" to=":j_w0" toLane="0" dir="s" state="M"/>'), encoding="utf-8")
    result = measure_added_class_movements(net, {"road_0": {"pedestrian"}, "alone_0": {"pedestrian"}})
    assert result["network_has_walkingareas"] is True
    assert result["additions"] == {"road_0": {"pedestrian": "usable"}, "alone_0": {"pedestrian": "no_loaded_movement"}}


def test_patch_handles_non_self_closing_lanes_quotes_and_escaped_names():
    raw = b'''<net><edge id="a"><lane id='a_0' type="a&gt;b" allow='passenger'>
      <param key="note" value="unrelated"/></lane></edge></net>'''
    result = _patch_lane_permissions(raw, ET.fromstring(raw), {"a_0": {"allow": "bus"}})
    assert result == raw.replace(b" allow='passenger'", b' allow="bus"')


@pytest.mark.parametrize("comment", [b"", b'<!-- <lane id="c" allow="x<y"> -->', b'<!-- <lane id="c" allow="x<y -->'])
def test_streamed_patch_matches_the_whole_file_patch_at_every_block_size(tmp_path, monkeypatch, comment):
    from astra_sumo.core import osm_access, xml_stream

    raw = b'''<?xml version="1.0"?>\n''' + comment + b'''
<net><edge id="a"><lane id="a_0" index="0" note="x>y" allow="passenger"/>
    <lane id='a_1' index="1" disallow="pedestrian">
      <param key="origId" value="7"/></lane></edge>
  <edge id=":j_0" function="internal"><lane id=":j_0_0" index="0"/></edge>
</net>'''
    source = tmp_path / "source.net.xml"
    source.write_bytes(raw)
    expected = {"a_0": {"disallow": "truck"}, "a_1": {"allow": "bicycle"}}
    try:
        whole = _patch_lane_permissions(raw, ET.fromstring(raw), expected)
    except (ValueError, ET.ParseError) as exc:
        whole = exc
    for size in range(1, len(raw) + 1, 3):
        monkeypatch.setattr(osm_access, "read_blocks", lambda handle, _=None, size=size: xml_stream.read_blocks(handle, size))
        candidate = tmp_path / f"candidate-{size}.net.xml"
        if isinstance(whole, Exception):
            with pytest.raises(type(whole), match=re.escape(str(whole))):
                osm_access._patch_lane_attributes_file(source, candidate, expected, {"allow", "disallow"})
            assert not candidate.exists()
        else:
            osm_access._patch_lane_attributes_file(source, candidate, expected, {"allow", "disallow"})
            assert candidate.read_bytes() == whole


def test_comment_that_imitates_a_lane_tag_is_rejected(tmp_path):
    osm, net = _inputs(tmp_path)
    original = net.read_bytes().replace(b"<net>", b'<!-- <lane id="fake"/> --><net>')
    net.write_bytes(original)
    with pytest.raises(ValueError, match="opening tags"):
        correct_osm_access_permissions(osm_file=osm, net_file=net, output_dir=tmp_path / "edit", command_runner=_load_ok)
    assert net.read_bytes() == original


@pytest.mark.parametrize("correction,status,reason", [
    ({"status": "pass", "connectivity_review_required": False,
      "added_class_movements": {"reverted_count": 2, "reverted_by_class": {"bicycle": 2}}}, "pass", None),
    ({"status": "review_required", "connectivity_review_required": True,
      "review": [{"lane_id": "a_0", "added_vehicle_classes": ["taxi"], "reason": "newly_allowed_modes_require_connectivity_review"}]},
     "review_required", "newly_allowed_modes_require_connectivity_review"),
    ({"status": "review_required", "connectivity_review_required": False,
      "review": [{"way_id": "7", "findings": [{"mode": "bus", "reason": "conditional_time_window_without_scenario_time"}]}]},
     "review_required", "osm_access_findings_open"),
])
def test_import_status_follows_the_measured_access_result(tmp_path, monkeypatch, correction, status, reason):
    from astra_sumo.core import osm_access
    from astra_sumo.core.osm_network import build_osm_network

    binary = shutil.which("netconvert")
    if not binary:
        pytest.skip("Native netconvert is required.")
    monkeypatch.setattr(osm_access, "correct_osm_access_permissions", lambda **kwargs: correction)
    result = build_osm_network(bbox="9.985,53.554,9.993,53.560", output_dir=tmp_path / "build",
        source_osm_path=Path(__file__).parent / "fixtures/osm_bus_access_118.osm.xml",
        allowed_highways={"service"}, clip_source_ways_to_bbox=False, netconvert_binary=binary)
    assert result["status"] == status
    assert result.get("reason") == reason
    assert result["claim_status"] == "diagnostic-demo"
    assert any("bicycle 2" in warning for warning in result["warnings"]) is (status == "pass")
    manifest = json.loads(Path(result["build_manifest_file"]).read_text(encoding="utf-8"))
    assert manifest["status"] == status


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
