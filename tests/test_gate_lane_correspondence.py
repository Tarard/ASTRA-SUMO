from copy import deepcopy
import json
import xml.etree.ElementTree as ET

from astra_sumo.core.candidate_contracts import file_sha256
from astra_sumo.core.corridor_edit_ledger import _bound_source_lane_indices, _modal_connectivity_gate
from astra_sumo.core.corridor_simplification import audit_alias_normalized_connections


def _shifted():
    before = ET.fromstring(
        '<net version="1.20"><edge id="in"><lane id="in_0" allow="passenger"/>'
        '<lane id="in_1" allow="passenger"/></edge><edge id="out"><lane id="out_0" allow="passenger"/></edge>'
        '<connection from="in" fromLane="0" to="out" toLane="0" tl="signal" linkIndex="0" dir="s"/>'
        '<connection from="in" fromLane="1" to="out" toLane="0" tl="signal" linkIndex="1" dir="s"/></net>'
    )
    after = deepcopy(before)
    edge = after.find("edge[@id='in']")
    edge.insert(0, ET.Element("lane", id="in_new", allow="passenger"))
    for index, lane in enumerate(edge.findall("lane")):
        lane.set("id", f"in_{index}")
    for row in after.findall("connection"):
        row.set("fromLane", str(int(row.get("fromLane")) + 1))
    return before, after


def test_declared_index_alias_does_not_hide_changed_signal_binding(tmp_path):
    before, after = _shifted()
    source, candidate = tmp_path / "source.net.xml", tmp_path / "candidate.net.xml"
    ET.ElementTree(before).write(source)
    ET.ElementTree(after).write(candidate)
    assert audit_alias_normalized_connections(source, candidate)["status"] == "fail"
    mapping = {"in": {"0": "1", "1": "2"}}
    assert audit_alias_normalized_connections(source, candidate, source_lane_indices=mapping)["status"] == "pass"
    after.findall("connection")[1].set("linkIndex", "0")
    ET.ElementTree(after).write(candidate)
    assert audit_alias_normalized_connections(source, candidate, source_lane_indices=mapping)["status"] == "fail"


def test_lane_index_alias_requires_a_matching_plan_hash_and_source(tmp_path):
    before, after = _shifted()
    source = tmp_path / "source.net.xml"
    ET.ElementTree(before).write(source)
    mapping = {"0": 1, "1": 2}
    plan = dict(
        operation=dict(operation="set_lane_count", params=dict(edge_id="in", num_lanes=3, add_side="right")),
        source=dict(path=str(source), sha256=file_sha256(source)),
        lane_index_mapping=mapping,
    )
    path = tmp_path / "plan.json"
    path.write_text(json.dumps(plan), encoding="utf-8")
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps(dict(artifacts=[dict(kind="materialization_plan", path=str(path), sha256=file_sha256(path))])),
        encoding="utf-8",
    )
    report = dict(
        plan_file=str(path), manifest_file=str(manifest), semantic_validation=dict(lane_index_mapping=mapping)
    )
    result = _bound_source_lane_indices(report, dict(status="review_required"), source, before, after)
    assert result == {"in": {"0": "1", "1": "2"}}
    path.write_text(json.dumps({**plan, "lane_index_mapping": {"0": 2, "1": 1}}), encoding="utf-8")
    assert _bound_source_lane_indices(report, dict(status="review_required"), source, before, after) == {}


def _bicycle_path(split):
    root = ET.fromstring(
        '<net version="1.20"><edge id="in" from="a" to="j"><lane id="in_0" index="0" allow="bicycle"/></edge>'
        '<edge id="out" from="j" to="b"><lane id="out_0" index="0" allow="bicycle"/></edge></net>'
    )
    for name in [":j_0", ":j_1"] if split else [":j_0"]:
        edge = ET.SubElement(root, "edge", id=name, function="internal")
        ET.SubElement(edge, "lane", id=name + "_0", index="0", allow="bicycle")
    ET.SubElement(root, "connection", **{"from": "in", "fromLane": "0", "to": "out", "toLane": "0", "via": ":j_0_0"})
    ET.SubElement(
        root,
        "connection",
        **{"from": ":j_0", "fromLane": "0", "to": "out", "toLane": "0", **({"via": ":j_1_0"} if split else {})},
    )
    if split:
        ET.SubElement(root, "connection", **{"from": ":j_1", "fromLane": "0", "to": "out", "toLane": "0"})
    return root


def test_internal_path_segmentation_does_not_count_as_lost_bicycle_movement():
    result = _modal_connectivity_gate(_bicycle_path(True), _bicycle_path(False))
    assert result["status"] == "pass", result
    assert result["source"]["bicycle"]["connection_count"] == result["candidate"]["bicycle"]["connection_count"] == 1


def test_actual_loaded_bicycle_permission_loss_still_blocks():
    before, after = _bicycle_path(True), _bicycle_path(False)
    after.find("edge[@id=':j_0']/lane").set("allow", "passenger")
    result = _modal_connectivity_gate(before, after)
    assert result["status"] == "blocked"
    assert result["decreases"]["bicycle"]["connection_count"] == (1, 0)


def test_modal_permission_reader_uses_native_allow_precedence():
    from astra_sumo.core.corridor_edit_ledger import _permission_allows_mode

    assert _permission_allows_mode({}, {"allow": "pedestrian", "disallow": "pedestrian"}, "pedestrian")
    assert _permission_allows_mode({"disallow": "pedestrian"}, {"allow": "pedestrian"}, "pedestrian")
    assert _permission_allows_mode({}, {"allow": "public_transport"}, "bus")


def test_modal_lane_lookup_uses_native_ordinal_with_repeated_declared_indices():
    from astra_sumo.core.corridor_edit_ledger import _edge_lane_allows_modal_mode

    edge = ET.fromstring(
        '<edge id="area"><lane id="first" index="0" allow="bus"/>'
        '<lane id="second" index="0" allow="pedestrian"/></edge>'
    )
    assert _edge_lane_allows_modal_mode(edge, "1", "pedestrian")
    assert not _edge_lane_allows_modal_mode(edge, "0", "pedestrian")
