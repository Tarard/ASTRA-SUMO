from __future__ import annotations

from pathlib import Path
import shutil
import subprocess
import xml.etree.ElementTree as ET

import pytest

from astra_sumo.core.candidate_contracts import file_sha256, validate_materialization_evidence
from astra_sumo.core.corridor_edit_ledger import build_corridor_edit_ledger, materialize_corridor_edit_variant
from astra_sumo.core.network_edit_operations import SCOPED_EDIT_TYPES, validate_scoped_operation


def _source(tmp_path, kind):
    controlled = kind in {"relocate_tls_control", "demote_duplicate_tls"}
    duplicate = kind == "demote_duplicate_tls"
    attrs_a = 'type="traffic_light" tl="shared"' if controlled else 'type="priority"'
    attrs_b = 'type="traffic_light" tl="shared"' if duplicate else 'type="priority"'
    nodes = tmp_path / "source.nod.xml"
    edges = tmp_path / "source.edg.xml"
    connections = tmp_path / "source.con.xml"
    nodes.write_text(f'<nodes><node id="W" x="-100" y="0"/><node id="A" x="0" y="0" {attrs_a}/>'
        f'<node id="B" x="20" y="0" {attrs_b}/><node id="E" x="120" y="0"/></nodes>', encoding="utf-8")
    edges.write_text('<edges><edge id="in" from="W" to="A" allow="passenger bus"/>'
        '<edge id="middle" from="A" to="B" allow="passenger bus"/>'
        '<edge id="out" from="B" to="E" allow="passenger bus"/>'
        '<edge id="east" from="E" to="B" allow="passenger bus"/>'
        '<edge id="reverse" from="B" to="A" allow="passenger bus"/>'
        '<edge id="west" from="A" to="W" allow="passenger bus"/></edges>', encoding="utf-8")
    pairs = [("in", "middle"), ("middle", "out")]
    if not controlled:
        pairs += [("east", "reverse"), ("reverse", "west")]
        if kind != "add_lane_connection":
            pairs.append(("in", "west"))
    connections.write_text('<connections>' + ''.join(f'<connection from="{a}" to="{b}" fromLane="0" toLane="0"/>'
        for a, b in pairs) + '</connections>', encoding="utf-8")
    source = tmp_path / "source.net.xml"
    result = subprocess.run([shutil.which("netconvert"), "-n", str(nodes), "-e", str(edges), "-x", str(connections),
        "--offset.disable-normalization", "true", "-o", str(source)], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    return source


def _params(kind, source):
    pair = {"from": "in", "fromLane": 0, "to": "west", "toLane": 0}
    values = {
        "join_junctions": {"node_ids": ["A", "B"], "join_id": "joined"},
        "absorb_reciprocal_fragment": {"node_ids": ["A", "B"], "join_id": "joined", "maximum_length_m": 30},
        "suppress_turnaround": pair,
        "add_lane_connection": pair,
        "delete_lane_connection": pair,
        "demote_duplicate_tls": {"node_id": "A", "retained_node_id": "B"},
        "relocate_tls_control": {"from_node": "A", "to_node": "B", "tls_id": "shared",
            "link_mapping": [{"source": {"from": "in", "fromLane": 0, "to": "middle", "toLane": 0},
                              "target": {"from": "middle", "fromLane": 0, "to": "out", "toLane": 0}}]},
        "set_lane_permissions": {"lane_id": "middle_0", "allow": "bus"},
        "set_lane_width": {"lane_id": "middle_0", "width_m": 3.4},
        "set_edge_shape": {"edge_id": "middle", "shape": [[0, 0], [10, 2], [20, 0]]},
        "set_lane_count": {"edge_id": "middle", "num_lanes": 2},
        "move_stop_line": {"lane_id": "middle_0", "end_offset_m": 2},
        "set_speed": {"lane_id": "middle_0", "speed_m_s": 8},
    }
    if kind == "set_junction_shape":
        node = ET.parse(source).getroot().find("junction[@id='A']")
        x, y = float(node.get("x")), float(node.get("y"))
        values[kind] = {"node_id": "A", "shape": [[x - 3, y - 5], [x + 3, y - 5], [x + 3, y + 5], [x - 3, y + 5]]}
    if kind == "relocate_tls_control":
        root = ET.parse(source).getroot()
        sources = [row for row in root.findall("connection") if row.get("tl") == "shared"]
        targets = [row for row in root.findall("connection") if row.get("from") in {"middle", "east"} and not row.get("tl")]
        assert len(sources) == len(targets)
        def pair(row):
            return {"from": row.get("from"), "fromLane": int(row.get("fromLane")),
                    "to": row.get("to"), "toLane": int(row.get("toLane"))}
        values[kind]["link_mapping"] = [{"source": pair(a), "target": pair(b)} for a, b in zip(sources, targets)]
    return values[kind]


@pytest.mark.skipif(not shutil.which("netconvert") or not shutil.which("sumo"), reason="SUMO binaries required")
@pytest.mark.parametrize("kind", SCOPED_EDIT_TYPES)
def test_typed_edit_materializes_with_source_bound_review_and_geometry(tmp_path, kind):
    source = _source(tmp_path, kind)
    digest = file_sha256(source)
    operation = {"id": "test-edit", "operation": kind, "status": "accepted", "params": _params(kind, source),
                 "rationale": "Synthetic construction and preservation check, without a field-accuracy claim.",
                 "evidence": [{"path": str(source), "sha256": digest}],
                 "rollback": {"action": "discard_candidate", "source_sha256": digest}}
    assert validate_scoped_operation(operation, ET.parse(source).getroot()) == []
    ledger = build_corridor_edit_ledger(net_file=source, output_dir=tmp_path / "ledger",
        operations=[operation], include_auto_proposals=False)
    assert ledger["status"] == "pass", ledger
    result = materialize_corridor_edit_variant(output_dir=tmp_path / "candidate", ledger_file=Path(ledger["ledger_file"]),
        netconvert_binary=shutil.which("netconvert"))
    assert result["status"] == "pass", result
    assert result["semantic_validation"]["requested_effect_applied"] is True
    assert result["semantic_validation"]["geometry_preservation"]["status"] == "pass"
    assert file_sha256(source) == digest
    if kind == "set_edge_shape":
        before = source.read_bytes()
        old_lane = ET.fromstring(before).find("edge[@id='middle']/lane")
        new_lane = ET.parse(result["candidate_net_file"]).getroot().find("edge[@id='middle']/lane")
        old_y = [float(point.split(",")[1]) for point in old_lane.get("shape").split()]
        new_y = [float(point.split(",")[1]) for point in new_lane.get("shape").split()]
        assert max(new_y) > max(old_y) + 1
    checked = validate_materialization_evidence(result, source_net_file=source, candidate_net_file=Path(result["candidate_net_file"]))
    assert checked["status"] == "pass", checked
    if kind in {"relocate_tls_control", "demote_duplicate_tls"}:
        assert result["review_required"] is True
        assert result["map_review_required_location_ids"]
    if kind == "set_lane_permissions":
        assert result["command"] == []
        assert result["command_result"]["netconvert_rebuild_count"] == 0
        forged = tmp_path / "forged.net.xml"
        tree = ET.parse(result["candidate_net_file"])
        tree.getroot().find("edge[@id='out']/lane").set("length", "999")
        tree.write(forged, encoding="utf-8")
        checked = validate_materialization_evidence({**result, "candidate_net_file": str(forged),
            "candidate_sha256": file_sha256(forged)}, source_net_file=source, candidate_net_file=forged)
        assert checked["status"] == "blocked"
        assert any(row["code"] == "direct_permission_materialization_not_exact" for row in checked["errors"])


@pytest.mark.parametrize("kind", SCOPED_EDIT_TYPES)
def test_typed_edit_rejects_missing_preconditions(kind):
    assert validate_scoped_operation({"operation": kind, "params": {}})


@pytest.mark.skipif(not shutil.which("netconvert") or not shutil.which("sumo"), reason="SUMO binaries required")
@pytest.mark.parametrize("layout", ["remove_right", "add_right", "explicit_connections"])
def test_lane_count_edit_preserves_survivor_permissions_and_declared_connections(tmp_path, layout):
    source = _source(tmp_path, "set_lane_count")
    if layout == "remove_right":
        edges_file = tmp_path / "source.edg.xml"
        tree = ET.parse(edges_file)
        edge = tree.getroot().find("edge[@id='middle']")
        edge.set("numLanes", "2")
        ET.SubElement(edge, "lane", index="0", allow="bus")
        ET.SubElement(edge, "lane", index="1", allow="passenger")
        tree.write(edges_file, encoding="utf-8")
        connections_file = tmp_path / "source.con.xml"
        connections_tree = ET.parse(connections_file)
        for start, end, a, b in (("in", "middle", "0", "1"), ("middle", "out", "1", "0")):
            ET.SubElement(connections_tree.getroot(), "connection", **{"from": start, "to": end, "fromLane": a, "toLane": b})
        connections_tree.write(connections_file, encoding="utf-8")
        rebuilt = subprocess.run([shutil.which("netconvert"), "-n", str(tmp_path / "source.nod.xml"), "-e", str(edges_file),
            "-x", str(connections_file), "--offset.disable-normalization", "true", "-o", str(source)], capture_output=True, text=True, timeout=30)
        assert rebuilt.returncode == 0, rebuilt.stderr
    params = {"edge_id": "middle", "num_lanes": 1, "remove_index": 0} if layout == "remove_right" else {
        "edge_id": "middle", "num_lanes": 2, "add_side": "right" if layout == "add_right" else "left"}
    if layout == "explicit_connections":
        params["connections"] = [{"from": a, "fromLane": i, "to": b, "toLane": j}
            for a, i, b, j in (("in", 0, "middle", 0), ("in", 0, "middle", 1), ("middle", 0, "out", 0), ("middle", 1, "out", 0))]
    digest = file_sha256(source)
    operation = {"id": "lane-layout", "operation": "set_lane_count", "status": "accepted", "params": params,
        "rationale": "Native lane identity and connection check.", "evidence": [{"path": str(source), "sha256": digest}],
        "rollback": {"action": "discard_candidate"}}
    report = materialize_corridor_edit_variant(output_dir=tmp_path / "candidate",
        ledger={"source_net_file": str(source), "source_sha256": digest, "operations": [operation]},
        netconvert_binary=shutil.which("netconvert"))
    assert report["status"] == "pass", report
    actual = ET.parse(report["candidate_net_file"]).getroot()
    target_pairs = {(row.get("from"), row.get("fromLane"), row.get("to"), row.get("toLane"))
                    for row in actual.findall("connection") if "middle" in (row.get("from"), row.get("to"))
                    and not row.get("from", "").startswith(":") and not row.get("to", "").startswith(":")}
    if layout == "remove_right":
        assert actual.find("edge[@id='middle']/lane").get("allow") == "passenger"
        assert target_pairs == {("in", "0", "middle", "0"), ("middle", "0", "out", "0")}
        import json
        from astra_sumo.core.network_edit_applicability import verify_network_edit
        facts = {"schema": "astra.network-facts/v1", "site_id": "lane-remap", "target_date": "2026-10-15",
            "coordinate_system": {"type": "network_xy_m", "location": dict(ET.parse(source).getroot().find("location").attrib)},
            "sources": [{"id": "fixture", "path": str(source), "sha256": digest, "source_date": None,
                         "authority": "synthetic lane-permission fixture", "license": "MIT"}],
            "facts": [{"id": "surviving-car-lane", "kind": "lane_permissions", "selector": {"lane_id": "middle_1"},
                       "expected": ["passenger"], "source_ids": ["fixture"]}]}
        facts_path, ledger_path = tmp_path / "facts.json", tmp_path / "daily-ledger.json"
        facts_path.write_text(json.dumps(facts), encoding="utf-8")
        ledger_path.write_text(json.dumps({"source_net_file": str(source), "source_sha256": digest,
                                          "operations": [operation]}), encoding="utf-8")
        checked = verify_network_edit(ledger_file=ledger_path, output_dir=tmp_path / "daily", facts_file=facts_path,
                                      netconvert_binary=shutil.which("netconvert"))
        assert checked["status"] == "measured", checked
        assert checked["fact_selector_remap"]["lane_id_mapping"]["middle_1"] == "middle_0"
        assert checked["fact_deltas"][0]["before"] == checked["fact_deltas"][0]["after"] == "matched"
    elif layout == "add_right":
        assert target_pairs == {("in", "0", "middle", "1"), ("middle", "1", "out", "0")}
    else:
        assert target_pairs == {(row["from"], str(row["fromLane"]), row["to"], str(row["toLane"])) for row in params["connections"]}
    assert report["semantic_validation"]["connection_inventory_preserved"]


@pytest.mark.skipif(not shutil.which("netconvert") or not shutil.which("sumo"), reason="SUMO binaries required")
def test_repeated_stop_line_edits_use_absolute_setbacks_and_can_restore_the_lane_end(tmp_path):
    source = _source(tmp_path, "move_stop_line")
    original = ET.parse(source).getroot().find("edge[@id='middle']/lane")
    original_end = tuple(map(float, original.get("shape").split()[-1].split(",")))
    original_length = float(original.get("length"))
    for step, offset in enumerate((2, 4, 0)):
        digest = file_sha256(source)
        operation = {"id": "offset", "operation": "move_stop_line", "status": "accepted",
            "params": {"lane_id": "middle_0", "end_offset_m": offset}, "rationale": "Native absolute setback check.",
            "evidence": [{"path": str(source), "sha256": digest}], "rollback": {"action": "discard_candidate"}}
        report = materialize_corridor_edit_variant(output_dir=tmp_path / f"step-{step}",
            ledger={"source_net_file": str(source), "source_sha256": digest, "operations": [operation]},
            netconvert_binary=shutil.which("netconvert"))
        assert report["status"] == "pass", report
        assert report["materialization_method"] == "stop_line_geometry_only"
        assert report["command_result"]["netconvert_rebuild_count"] == 0
        assert report["semantic_validation"]["direct_stop_line_edit"]["all_other_bytes_preserved"]
        assert validate_materialization_evidence(report, source_net_file=source,
                                                candidate_net_file=Path(report["candidate_net_file"]))["status"] == "pass"
        source = Path(report["candidate_net_file"])
        lane = ET.parse(source).getroot().find("edge[@id='middle']/lane")
        endpoint = tuple(map(float, lane.get("shape").split()[-1].split(",")))
        assert original_end[0] - endpoint[0] == pytest.approx(offset)
        assert endpoint[1] == pytest.approx(original_end[1])
        assert float(lane.get("length")) == pytest.approx(original_length - offset)
    from astra_sumo.core.osm_access import _patch_lane_attributes
    source.write_bytes(_patch_lane_attributes(source.read_bytes(), ET.parse(source).getroot(), {"in_0": {"speed": "8"}}, {"speed"}))
    report["candidate_sha256"] = file_sha256(source)
    checked = validate_materialization_evidence(report, source_net_file=Path(report["source_net_file"]), candidate_net_file=source)
    assert checked["status"] == "blocked"
    assert any(row["code"] == "direct_stop_line_materialization_not_exact" for row in checked["errors"])


def test_direct_stop_line_preserves_waiting_structure_and_all_unrelated_bytes(tmp_path):
    from astra_sumo.core.network_edit_operations import _stop_line_patches, write_direct_lane_edit
    from shapely.geometry import LineString
    raw = b'''<net><!-- source waiting points remain authoritative -->
      <edge id="approach"><lane id="approach_0" index="0" length="10" shape="0,0 10,0"/></edge>
      <edge id=":j_0" function="internal"><lane id=":j_0_0" length="5" shape="10,0 13,0 15,0"/></edge>
      <edge id=":j_1" function="internal"><lane id=":j_1_0" length="8" shape="10,0 13,0 13,5"/></edge>
      <junction id="j" intLanes=":j_0_0 :j_1_0"><request index="0" response="10" foes="10"/></junction>
      <junction id=":j_wait" type="internal"/>
      <tlLogic id="j"><phase duration="10" state="Gr"/></tlLogic>
      <connection from="approach" fromLane="0" to="out" toLane="0" via=":j_0_0" tl="j" linkIndex="0"/>
      <connection from="approach" fromLane="0" to="left" toLane="0" via=":j_1_0" tl="j" linkIndex="1"/>
      </net>'''
    source, candidate, restored = [tmp_path / name for name in ("source.xml", "candidate.xml", "restored.xml")]
    source.write_bytes(raw)
    operation = {"operation": "move_stop_line", "params": {"lane_id": "approach_0", "end_offset_m": 2}}
    write_direct_lane_edit(source, candidate, operation)
    old, new = ET.fromstring(raw), ET.parse(candidate).getroot()
    for tag in ("junction", "tlLogic", "connection"):
        assert [ET.tostring(row) for row in new.findall(tag)] == [ET.tostring(row) for row in old.findall(tag)]
    for key, expected in (("approach_0", 8), (":j_0_0", 7), (":j_1_0", 10)):
        lane = new.find(f"edge/lane[@id='{key}']")
        assert float(lane.get("length")) == expected
    assert new.find("edge/lane[@id=':j_0_0']").get("shape").startswith("8.000000000000,0.000000000000")
    operation["params"]["end_offset_m"] = 0
    write_direct_lane_edit(candidate, restored, operation)
    final = ET.parse(restored).getroot()
    def shape(row):
        return LineString([tuple(map(float, p.split(","))) for p in row.get("shape").split()])
    for a, b in zip(old.findall("edge/lane"), final.findall("edge/lane")):
        assert shape(a).equals(shape(b))
        assert float(a.get("length")) == float(b.get("length"))
    assert source.read_bytes() == raw
    # Returning a setback past divergent internal prefixes cannot invent one lane end.
    old.find("edge/lane[@id='approach_0']").set("endOffset", "4")
    assert _stop_line_patches(old, {"lane_id": "approach_0", "end_offset_m": 0}) is None


def test_direct_stop_line_keeps_elevation_and_refuses_a_shared_internal_prefix(tmp_path):
    from astra_sumo.core.network_edit_operations import _stop_line_patches, write_direct_lane_edit
    source, candidate = tmp_path / "source.xml", tmp_path / "candidate.xml"
    root = ET.fromstring('''<net>
      <edge id="a"><lane id="a_0" index="0" length="10" shape="0,0,1 10,0,2"/></edge>
      <edge id=":j_0" function="internal"><lane id=":j_0_0" length="5" shape="10,0,2 15,0,2"/></edge>
      <edge id="other"><lane id="other_0" index="0" length="10" shape="0,1,1 10,1,2"/></edge>
      <connection from="a" fromLane="0" to="b" toLane="0" via=":j_0_0"/></net>''')
    ET.ElementTree(root).write(source, encoding="utf-8")
    params = {"lane_id": "a_0", "end_offset_m": 2}
    write_direct_lane_edit(source, candidate, {"operation": "move_stop_line", "params": params})
    lane = ET.parse(candidate).getroot().find("edge/lane[@id='a_0']")
    assert tuple(map(float, lane.get("shape").split()[-1].split(","))) == pytest.approx((8, 0, 1.8))
    ET.SubElement(root, "connection", **{"from": "other", "fromLane": "0", "to": "b", "toLane": "0", "via": ":j_0_0"})
    assert _stop_line_patches(root, params) is None
    root.findall("connection")[-1].set("from", ":other_internal")
    assert _stop_line_patches(root, params) is None


@pytest.mark.skipif(not shutil.which("netconvert") or not shutil.which("sumo"), reason="SUMO binaries required")
@pytest.mark.parametrize("kind", ["set_edge_shape", "set_lane_count"])
def test_road_edits_recompute_custom_lane_geometry_and_allow_lane_removal(tmp_path, kind):
    source = _source(tmp_path, kind)
    tree = ET.parse(source)
    edge = tree.getroot().find("edge[@id='middle']")
    if kind == "set_lane_count":
        from copy import deepcopy
        extra = deepcopy(edge.find("lane"))
        extra.set("id", "middle_1")
        extra.set("index", "1")
        extra.set("shape", "0,1.6 20,1.6")
        edge.append(extra)
    edge.find("lane").set("customShape", "true")
    tree.write(source, encoding="utf-8")
    params = _params(kind, source)
    if kind == "set_lane_count":
        params["num_lanes"] = 1
    digest = file_sha256(source)
    operation = {"id": "road-edit", "operation": kind, "status": "accepted", "params": params,
        "rationale": "Native custom lane geometry check.", "evidence": [{"path": str(source), "sha256": digest}],
        "rollback": {"action": "discard_candidate"}}
    report = materialize_corridor_edit_variant(output_dir=tmp_path / "candidate",
        ledger={"source_net_file": str(source), "source_sha256": digest, "operations": [operation]},
        netconvert_binary=shutil.which("netconvert"))
    assert report["status"] == "pass", report
    lanes = ET.parse(report["candidate_net_file"]).getroot().find("edge[@id='middle']").findall("lane")
    assert len(lanes) == 1
    if kind == "set_edge_shape":
        assert max(float(point.split(",")[1]) for point in lanes[0].get("shape").split()) > -0.6


def test_reciprocal_absorption_requires_a_declared_length_bound():
    operation = {"operation": "absorb_reciprocal_fragment", "params": {"node_ids": ["A", "B"], "join_id": "joined"}}
    assert validate_scoped_operation(operation)


def test_typed_edit_rejects_ignored_parameters():
    assert validate_scoped_operation({"operation": "set_lane_width", "params": {"lane_id": "x_0", "width_m": 3.4, "ignored": True}})


@pytest.mark.skipif(not shutil.which("netconvert"), reason="netconvert required")
def test_typed_materializer_requires_the_accepted_source_hash(tmp_path):
    source = _source(tmp_path, "set_lane_width")
    operation = {"id": "width", "operation": "set_lane_width", "status": "accepted",
        "params": {"lane_id": "middle_0", "width_m": 3.4}, "rationale": "Synthetic check.",
        "evidence": [{"path": str(source), "sha256": file_sha256(source)}], "rollback": {"action": "discard_candidate"}}
    result = materialize_corridor_edit_variant(output_dir=tmp_path / "candidate",
        ledger={"source_net_file": str(source), "operations": [operation]})
    assert result["status"] == "blocked"
    assert any(row["code"] == "scoped_edit_requires_matching_source_sha256" for row in result["validation"]["errors"])


@pytest.mark.skipif(not shutil.which("netconvert"), reason="netconvert required")
@pytest.mark.parametrize("kind", ["join_junctions", "absorb_reciprocal_fragment"])
def test_join_cannot_absorb_a_bridge_declared_in_source_parameters(tmp_path, kind):
    source = _source(tmp_path, kind)
    root = ET.parse(source).getroot()
    ET.SubElement(root.find("edge[@id='middle']"), "param", key="bridge", value="yes")
    errors = validate_scoped_operation({"operation": kind, "params": _params(kind, source)}, root)
    assert errors and "protected operation" in errors[0]["detail"]


@pytest.mark.skipif(not shutil.which("netconvert"), reason="netconvert required")
def test_static_signal_move_does_not_claim_actuated_detector_correspondence(tmp_path):
    source = _source(tmp_path, "relocate_tls_control")
    root = ET.parse(source).getroot()
    root.find("tlLogic").set("type", "actuated")
    errors = validate_scoped_operation({"operation": "relocate_tls_control", "params": _params("relocate_tls_control", source)}, root)
    assert errors and "detector-correspondence" in errors[0]["detail"]


@pytest.mark.skipif(not shutil.which("netconvert"), reason="netconvert required")
def test_existing_edge_deletion_uses_the_shared_exterior_protection(tmp_path):
    from astra_sumo.core.command_runner import run_command

    source = _source(tmp_path, "join_junctions")
    tree = ET.parse(source)
    root = tree.getroot()
    remote = ET.Element("edge", id="remote", **{"from": "R", "to": "S", "priority": "1"})
    ET.SubElement(remote, "lane", id="remote_0", index="0", speed="10", length="50", shape="-500,0 -450,0")
    root.insert(next(i for i, item in enumerate(root) if item.tag == "junction"), remote)
    for name, x, incoming in (("R", "-500", ""), ("S", "-450", "remote_0")):
        node = ET.Element("junction", id=name, type="dead_end", x=x, y="0", incLanes=incoming, intLanes="", shape="")
        root.insert(next((i for i, item in enumerate(root) if item.tag == "connection"), len(root)), node)
    tree.write(source, encoding="utf-8", xml_declaration=True)
    digest = file_sha256(source)
    operation = {"id": "delete-west", "operation": "delete_edge", "status": "accepted", "target_ids": ["west"],
        "rationale": "Synthetic deletion with a separate remote geometry fault.",
        "evidence": [{"path": str(source), "sha256": digest}], "rollback": {"action": "discard_candidate"}}
    def shifted_conversion(command, **kwargs):
        result = run_command(command, **kwargs)
        assert result.returncode == 0, result.stderr
        file = Path(command[command.index("--output-file") + 1])
        changed = ET.parse(file)
        lane = changed.getroot().find("edge[@id='remote']/lane")
        lane.set("length", "27.84")
        lane.set("shape", "-477.84,0 -450,0")
        changed.write(file, encoding="utf-8", xml_declaration=True)
        return result
    result = materialize_corridor_edit_variant(output_dir=tmp_path / "candidate",
        ledger={"source_net_file": str(source), "source_sha256": digest, "operations": [operation]},
        netconvert_binary=shutil.which("netconvert"), command_runner=shifted_conversion)
    assert result["status"] == "pass", result
    assert result["semantic_validation"]["geometry_preservation"]["status"] == "pass"
    actual = ET.parse(result["candidate_net_file"]).getroot()
    assert actual.find("edge[@id='west']") is None
    assert actual.find("edge[@id='remote']/lane").get("length") == "50"
    assert actual.find("edge[@id='remote']/lane").get("shape") == "-500,0 -450,0"
    assert file_sha256(source) == digest


@pytest.mark.skipif(not shutil.which("netconvert") or not shutil.which("sumo"), reason="SUMO binaries required")
def test_typed_signalized_join_requires_explicit_diagnostic_policy(tmp_path):
    source = _source(tmp_path, "demote_duplicate_tls")
    digest = file_sha256(source)
    params = {"node_ids": ["A", "B"], "join_id": "joined", "signal_policy": "rebuild_diagnostic"}
    operation = {"id": "joined-controls", "operation": "join_junctions", "status": "accepted", "params": params,
        "rationale": "Synthetic controlled join with diagnostic timing only.",
        "evidence": [{"path": str(source), "sha256": digest}], "rollback": {"action": "discard_candidate"}}
    result = materialize_corridor_edit_variant(output_dir=tmp_path / "candidate",
        ledger={"source_net_file": str(source), "source_sha256": digest, "operations": [operation]},
        netconvert_binary=shutil.which("netconvert"))
    assert result["status"] == "pass", result
    assert result["semantic_validation"]["restoration"]["gates"]["boundary_coverage"] is True
    assert file_sha256(source) == digest


@pytest.mark.skipif(not shutil.which("netconvert"), reason="netconvert required")
def test_duplicate_signal_validation_counts_internal_pedestrian_controllers(tmp_path):
    source = _source(tmp_path, "demote_duplicate_tls")
    root = ET.parse(source).getroot()
    for identity, function in ((":A_w0", "walkingarea"), (":A_c0", "crossing")):
        edge = ET.SubElement(root, "edge", id=identity, function=function)
        ET.SubElement(edge, "lane", id=identity + "_0", index="0", allow="pedestrian")
    ET.SubElement(root, "connection", **{"from": ":A_w0", "to": ":A_c0", "fromLane": "0", "toLane": "0",
        "tl": "pedestrian-only-controller", "linkIndex": "0"})
    operation = {"operation": "demote_duplicate_tls", "params": {"node_id": "A", "retained_node_id": "B"}}
    assert validate_scoped_operation(operation, root)


@pytest.mark.skipif(not shutil.which("netconvert"), reason="netconvert required")
def test_scoped_evidence_hash_mismatch_stops_before_conversion(tmp_path):
    source = _source(tmp_path, "set_lane_width")
    fact = tmp_path / "width-fact.json"
    fact.write_text('{"width_m": 3.4}', encoding="utf-8")
    digest = file_sha256(source)
    operation = {"id": "width", "operation": "set_lane_width", "status": "accepted",
        "params": {"lane_id": "middle_0", "width_m": 3.4}, "rationale": "Synthetic width check.",
        "evidence": [{"path": str(fact), "sha256": "0" * 64}], "rollback": {"action": "discard_candidate"}}
    result = materialize_corridor_edit_variant(output_dir=tmp_path / "candidate",
        ledger={"source_net_file": str(source), "source_sha256": digest, "operations": [operation]})
    assert result["status"] == "blocked"
    assert result["materialization_status"] == "invalid_scoped_plan"
    assert "evidence hash" in result["error"]
    assert file_sha256(source) == digest
