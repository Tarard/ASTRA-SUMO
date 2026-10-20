from __future__ import annotations

import json
import gzip
from pathlib import Path
import shutil
import subprocess
import xml.etree.ElementTree as ET

import pytest

from astra_sumo.core.candidate_contracts import file_sha256, validate_materialization_evidence
from astra_sumo.core.corridor_edit_ledger import build_corridor_edit_ledger, materialize_corridor_edit_variant
from astra_sumo.core.network_edit_operations import SCOPED_EDIT_TYPES, validate_scoped_operation


@pytest.mark.parametrize("explicit", [False, True])
def test_lane_count_remapping_retains_real_hamburg_custom_movements(explicit):
    from astra_sumo.core.network_edit_operations import _inventory, _lane_count_connections

    fixture = Path(__file__).parents[1] / "benchmarks/hamburg_loaded_links_20261018_v1/candidate.net.xml.gz"
    root = ET.fromstring(gzip.decompress(fixture.read_bytes()))
    edges, _, connections = _inventory(root)
    target = "554713076#0"
    params = {"edge_id": target, "num_lanes": 5, "add_side": "right"}
    original = [row for pair, row in connections.items()
                if target in (pair[0], pair[2]) and pair[0] in edges and pair[2] in edges]
    if explicit:
        params["connections"] = [{key: int(row.get(key)) + (row.get(edge) == target) if key.endswith("Lane") else row.get(key)
                                  for key, edge in (("from", "from"), ("fromLane", "from"), ("to", "to"), ("toLane", "to"))}
                                 for row in original]
    records = _lane_count_connections(params, edges, connections, network_version=root.get("version"))
    indexed = {tuple(row[key] for key in ("from", "fromLane", "to", "toLane")): row for row in records}
    assert sum(bool(row.get("shape")) for row in original) == 2
    for row in original:
        key = (row.get("from"), str(int(row.get("fromLane")) + (row.get("from") == target)),
               row.get("to"), str(int(row.get("toLane")) + (row.get("to") == target)))
        actual = indexed[key]
        for attribute in ("shape", "length", "speed", "tl", "linkIndex", "linkIndex2", "keepClear", "contPos"):
            if row.get(attribute) is not None:
                assert actual.get(attribute) == row.get(attribute), (key, attribute)
        assert not {"via", "dir", "state"} & actual.keys()


def test_lane_count_remapping_preserves_spatial_and_driving_constraints():
    from astra_sumo.core.network_edit_operations import _inventory, _lane_count_connections

    root = ET.fromstring('<net version="1.20"><edge id="in"><lane id="in_0"/></edge>'
        '<edge id="out"><lane id="out_0"/></edge>'
        '<connection from="in" fromLane="0" to="out" toLane="0" via=":j_0_0" dir="s" state="M" '
        'shape="0,0,0 1,2,3 4,0,5" length="8.7" speed="3.2" contPos="2.5" visibility="11" '
        'keepClear="false" pass="true" changeLeft="bus" changeRight="passenger"/></net>')
    edges, _, connections = _inventory(root)
    records = _lane_count_connections({"edge_id": "out", "num_lanes": 2, "add_side": "right"}, edges, connections)
    assert len(records) == 1
    original = root.find('connection').attrib
    assert records[0] == {key: "1" if key == "toLane" else value for key,value in original.items()
                          if key not in {"via", "dir", "state"}}


@pytest.mark.parametrize("source_permissions,requested", [({"allow": "bus"}, {"disallow": "bus"}),
                                                         ({"disallow": "bus"}, {"allow": "bus"})])
def test_explicit_lane_count_permissions_replace_inherited_directives(source_permissions, requested):
    from astra_sumo.core.network_edit_operations import _inventory, _lane_count_connections

    root = ET.fromstring('<net version="1.20"><edge id="in"><lane id="in_0"/></edge>'
        '<edge id="out"><lane id="out_0"/></edge>'
        '<connection from="in" fromLane="0" to="out" toLane="0" shape="0,0 1,1 2,0"/></net>')
    root.find('connection').attrib.update(source_permissions)
    edges, _, connections = _inventory(root)
    row = {"from": "in", "fromLane": 0, "to": "out", "toLane": 0, **requested}
    records = _lane_count_connections({"edge_id": "out", "num_lanes": 2, "connections": [row]},
                                      edges, connections, network_version=root.get("version"))
    assert {key:value for key,value in records[0].items() if key in {"allow", "disallow"}} == requested
    assert records[0]["shape"] == "0,0 1,1 2,0"


@pytest.mark.parametrize("count", [3, 4, 5])
def test_real_hamburg_lane_count_plan_keeps_existing_signal_programs(tmp_path, count):
    from astra_sumo.core.network_edit_operations import write_scoped_edit_plan

    fixture = Path(__file__).parents[1] / "benchmarks/hamburg_loaded_links_20261018_v1/candidate.net.xml.gz"
    source = tmp_path / "source.net.xml"
    source.write_bytes(gzip.decompress(fixture.read_bytes()))
    root = ET.parse(source).getroot()
    output = tmp_path / "edit"
    output.mkdir()
    plan = write_scoped_edit_plan(root=root, operation={"id": "count", "operation": "set_lane_count",
        "params": {"edge_id": "554713076#0", "num_lanes": count}}, source_file=source,
        candidate_file=output / "candidate.net.xml", output_dir=output, prefix="count",
        netconvert_binary="netconvert", mode="explore")
    assert "tllogics" in plan["patches"]
    patch = ET.parse(plan["patches"]["tllogics"]["path"]).getroot()
    original = root.find("tlLogic[@id='LSA2349_part0']")
    retained = patch.find("tlLogic[@id='LSA2349_part0']")
    assert retained is not None and len(retained.findall("phase")) == 12
    assert [(row.tag, row.attrib, (row.text or "").strip()) for row in retained.iter()] == [
        (row.tag, row.attrib, (row.text or "").strip()) for row in original.iter()]
    assert "--tllogic-files" in plan["command"]


@pytest.mark.skipif(not shutil.which("netconvert") or not shutil.which("sumo"), reason="SUMO binaries required")
@pytest.mark.parametrize("count,layout", [(3, {}), (4, {}), (5, {}),
    (5, {"add_side": "right"}), (3, {"remove_index": 0}),
    (5, {"add_side": "right", "vehicle_class": "passenger"})],
    ids=["remove_high", "identity", "add_left", "add_right", "remove_right", "add_passenger_right"])
def test_real_hamburg_lane_count_native_output_keeps_signal_phases(tmp_path, count, layout):
    fixture = Path(__file__).parents[1] / "benchmarks/hamburg_loaded_links_20261018_v1/candidate.net.xml.gz"
    source = tmp_path / "source.net.xml"
    source.write_bytes(gzip.decompress(fixture.read_bytes()))
    digest = file_sha256(source)
    operation = {"id": "count", "operation": "set_lane_count", "status": "accepted",
        "params": {"edge_id": "554713076#0", "num_lanes": count, **layout},
        "rationale": "Regression: preserve published source signal programs during a lane-count edit.",
        "evidence": [{"path": str(source), "sha256": digest}], "rollback": {"action": "discard_candidate"}}
    report = materialize_corridor_edit_variant(output_dir=tmp_path / "candidate",
        ledger={"source_net_file": str(source), "source_sha256": digest, "operations": [operation]},
        netconvert_binary=shutil.which("netconvert"))
    nonmotor_access_loss = ((layout.get("add_side") == "right" and "vehicle_class" not in layout)
                           or layout.get("remove_index") == 0)
    expected_status = "blocked" if nonmotor_access_loss else "review_required" if count == 5 else "pass"
    assert report["status"] == expected_status, report
    access = report["semantic_validation"]["nonmotor_access_preservation"]
    assert access["status"] == ("fail" if nonmotor_access_loss else "pass"), access
    if layout.get("remove_index") == 0:
        assert access["declared_removed_lane_ids"] == ["554713076#0_0"]
        assert {change["source_lane"] for change in access["changes"]} == {"127716467#1_0", "60578487#3_0"}
    assert report["semantic_validation"]["sumo_load"]["status"] == "pass"
    old = ET.parse(source).getroot()
    new = ET.parse(report["candidate_net_file"]).getroot()
    before = old.find("tlLogic[@id='LSA2349_part0']")
    after = new.find("tlLogic[@id='LSA2349_part0']")
    if layout.get("vehicle_class") == "passenger":
        from astra_sumo.core.osm_access import _permission_set
        lanes = new.find("edge[@id='554713076#0']").findall('lane')
        modes = [_permission_set(lane.attrib,network_version=new.get('version')) for lane in lanes]
        assert modes[0] == {"pedestrian"} and modes[1] == {"bicycle"}
        assert sum('passenger' in mode for mode in modes) == 3
    assert after is not None and after.get("type") == before.get("type")
    assert float(after.get("offset")) == float(before.get("offset"))
    assert len(before.findall("phase")) == len(after.findall("phase")) == 12
    for expected, actual in zip(before.findall("phase"), after.findall("phase")):
        assert float(actual.get("duration")) == float(expected.get("duration"))
        assert {key:value for key,value in expected.attrib.items() if key != "duration"}.items() <= actual.attrib.items()
    retained = [row for row in new.findall("connection") if row.get("from") == "554713076#0" and row.get("shape")]
    assert len(retained) == (1 if count == 3 and not layout else 2)
    actual_connections = {(row.get("fromLane"),row.get("to"),row.get("toLane")):row
                          for row in new.findall("connection") if row.get("from") == "554713076#0"}
    shift = 1 if layout.get("add_side") == "right" else -1 if layout.get("remove_index") == 0 else 0
    for row in old.findall("connection"):
        if row.get("from") != "554713076#0" or not row.get("tl"):
            continue
        index = int(row.get("fromLane"))
        if count == 3 and not layout and index >= count:
            continue
        actual_shift = shift if layout.get("vehicle_class") != "passenger" or index >= 2 else 0
        actual = actual_connections[str(index+actual_shift),row.get("to"),row.get("toLane")]
        assert (actual.get("tl"),actual.get("linkIndex")) == (row.get("tl"),row.get("linkIndex"))
    assert file_sha256(source) == digest


def _source(tmp_path, kind, remote_controller=None):
    controlled = kind in {"relocate_tls_control", "demote_duplicate_tls"}
    duplicate = kind == "demote_duplicate_tls"
    attrs_a = 'type="traffic_light" tl="shared"' if controlled else 'type="priority"'
    attrs_b = 'type="traffic_light" tl="shared"' if duplicate else 'type="priority"'
    nodes = tmp_path / "source.nod.xml"
    edges = tmp_path / "source.edg.xml"
    connections = tmp_path / "source.con.xml"
    remote_nodes = (f'<node id="RX" x="1000" y="0"/><node id="RY" x="1010" y="0" '
                    f'type="traffic_light" tl="{remote_controller}"/><node id="RZ" x="1020" y="0"/>') if remote_controller else ''
    nodes.write_text(f'<nodes><node id="W" x="-100" y="0"/><node id="A" x="0" y="0" {attrs_a}/>'
        f'<node id="B" x="20" y="0" {attrs_b}/><node id="E" x="120" y="0"/>{remote_nodes}</nodes>', encoding="utf-8")
    remote_edges = ('<edge id="remote-in" from="RX" to="RY" allow="passenger bus"/>'
                    '<edge id="remote-out" from="RY" to="RZ" allow="passenger bus"/>') if remote_controller else ''
    edges.write_text('<edges><edge id="in" from="W" to="A" allow="passenger bus"/>'
        '<edge id="middle" from="A" to="B" allow="passenger bus"/>'
        '<edge id="out" from="B" to="E" allow="passenger bus"/>'
        '<edge id="east" from="E" to="B" allow="passenger bus"/>'
        '<edge id="reverse" from="B" to="A" allow="passenger bus"/>'
        f'<edge id="west" from="A" to="W" allow="passenger bus"/>{remote_edges}</edges>', encoding="utf-8")
    pairs = [("in", "middle"), ("middle", "out")]
    if remote_controller:
        pairs.append(("remote-in", "remote-out"))
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
@pytest.mark.parametrize("kind,remote_controller", [
    ("demote_duplicate_tls", "remote"), ("relocate_tls_control", "remote"),
    ("demote_duplicate_tls", "shared"),
])
def test_signal_edits_use_local_conversion_unless_controller_crosses_boundary(tmp_path, kind, remote_controller):
    source = _source(tmp_path, kind, remote_controller)
    digest = file_sha256(source)
    operation = {"id": "signal", "operation": kind, "status": "accepted", "params": _params(kind, source),
                 "rationale": "Synthetic scoped signal reconstruction.",
                 "evidence": [{"path": str(source), "sha256": digest}], "rollback": {"action": "discard_candidate"}}
    report = materialize_corridor_edit_variant(output_dir=tmp_path / "candidate",
        ledger={"source_net_file": str(source), "source_sha256": digest, "operations": [operation]},
        netconvert_binary=shutil.which("netconvert"))
    assert report["status"] == "pass", report
    plan = json.loads(Path(report["plan_file"]).read_text(encoding="utf-8"))
    scope = plan["local_conversion"]
    if remote_controller == "shared":
        assert scope["status"] == "full_network" and scope["reason"] == "controller_spans_edit_boundary"
    else:
        assert scope["status"] == "local"
        assert not {"remote-in", "remote-out"} & set(scope["context_edge_ids"])
        assert scope["converted_normal_edge_count"] < scope["source_normal_edge_count"]
        before, after = ET.parse(source).getroot(), ET.parse(report["candidate_net_file"]).getroot()
        for selector in ("tlLogic[@id='remote']", "edge[@id='remote-in']", "edge[@id='remote-out']", "junction[@id='RY']"):
            assert ET.tostring(before.find(selector)) == ET.tostring(after.find(selector)), selector
    assert report["semantic_validation"]["loaded_link_preservation"]["status"] == "pass"
    assert file_sha256(source) == digest


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
    assert result["status"] == ("review_required" if kind == "set_lane_count" else "pass"), result
    assert result["semantic_validation"]["requested_effect_applied"] is True
    assert result["semantic_validation"]["geometry_preservation"]["status"] == "pass"
    loaded = result["semantic_validation"]["loaded_link_preservation"]
    assert loaded["status"] == "pass", loaded
    assert loaded["inputs"]["candidate"]["sha256"] == file_sha256(Path(result["candidate_net_file"]))
    artifacts = json.loads(Path(result["manifest_file"]).read_text())["artifacts"]
    assert {"loaded_link_preservation", "loaded_link_request", "loaded_link_observations"} <= {row["kind"] for row in artifacts}
    assert file_sha256(source) == digest
    if kind == "set_edge_shape":
        before = source.read_bytes()
        old_lane = ET.fromstring(before).find("edge[@id='middle']/lane")
        new_lane = ET.parse(result["candidate_net_file"]).getroot().find("edge[@id='middle']/lane")
        old_y = [float(point.split(",")[1]) for point in old_lane.get("shape").split()]
        new_y = [float(point.split(",")[1]) for point in new_lane.get("shape").split()]
        assert max(new_y) > max(old_y) + 1
    checked = validate_materialization_evidence(result, source_net_file=source, candidate_net_file=Path(result["candidate_net_file"]))
    assert checked["status"] == ("review_required" if kind == "set_lane_count" else "pass"), checked
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
    assert report["status"] == ("review_required" if layout == "add_right" else "pass"), report
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
        assert report["materialization_method"] == "stop_line_edge_mean_3d"
        assert report["command_result"]["netconvert_rebuild_count"] == 0
        assert report["semantic_validation"]["direct_stop_line_edit"]["all_other_bytes_preserved"]
        assert validate_materialization_evidence(report, source_net_file=source,
                                                candidate_net_file=Path(report["candidate_net_file"]))["status"] == "pass"
        legacy = validate_materialization_evidence({**report, "materialization_method": "stop_line_geometry_only"},
            source_net_file=source, candidate_net_file=Path(report["candidate_net_file"]))
        assert any(row["code"] == "legacy_stop_line_length_convention_requires_rebuild" for row in legacy["errors"])
        source = Path(report["candidate_net_file"])
        lane = ET.parse(source).getroot().find("edge[@id='middle']/lane")
        endpoint = tuple(map(float, lane.get("shape").split()[-1].split(",")))
        assert original_end[0] - endpoint[0] == pytest.approx(offset)
        assert endpoint[1] == pytest.approx(original_end[1])
        assert float(lane.get("length")) == pytest.approx(original_length - offset)
    from astra_sumo.core.osm_access import _patch_lane_attributes
    from copy import deepcopy
    forged = deepcopy(report)
    forged["semantic_validation"]["direct_stop_line_edit"]["length_convention"] = "per_lane"
    checked = validate_materialization_evidence(forged, source_net_file=Path(report["source_net_file"]), candidate_net_file=source)
    assert any(row["code"] == "direct_stop_line_materialization_not_exact" for row in checked["errors"])
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
      <edge id="a"><lane id="a_0" index="0" length="10.049875621121" shape="0,0,1 10,0,2"/></edge>
      <edge id=":j_0" function="internal"><lane id=":j_0_0" length="5" shape="10,0,2 15,0,2"/></edge>
      <edge id="other"><lane id="other_0" index="0" length="10" shape="0,1,1 10,1,2"/></edge>
      <connection from="a" fromLane="0" to="b" toLane="0" via=":j_0_0"/></net>''')
    ET.ElementTree(root).write(source, encoding="utf-8")
    params = {"lane_id": "a_0", "end_offset_m": 2}
    write_direct_lane_edit(source, candidate, {"operation": "move_stop_line", "params": params})
    lane = ET.parse(candidate).getroot().find("edge/lane[@id='a_0']")
    import math
    assert tuple(map(float, lane.get("shape").split()[-1].split(","))) == pytest.approx(
        (10 - 20 / math.sqrt(101), 0, 2 - 2 / math.sqrt(101)))
    ET.SubElement(root, "connection", **{"from": "other", "fromLane": "0", "to": "b", "toLane": "0", "via": ":j_0_0"})
    assert _stop_line_patches(root, params) is None
    root.findall("connection")[-1].set("from", ":other_internal")
    assert _stop_line_patches(root, params) is None


def test_lane_count_fact_mapping_uses_actual_lane_membership_instead_of_an_edge_prefix(tmp_path):
    import json
    from astra_sumo.core.network_edit_operations import _remap_lane_count_facts
    source = tmp_path / "source.xml"
    source.write_text('''<net><edge id="a"><lane id="a_0" index="0"/><lane id="a_1" index="1"/></edge>
        <edge id="a_branch"><lane id="a_branch_0" index="0"/></edge></net>''', encoding="utf-8")
    facts = tmp_path / "facts.json"
    facts.write_text(json.dumps({"sources": [], "facts": [
        {"id": "survivor", "kind": "speed", "selector": {"lane_id": "a_1"}, "expected": 10},
        {"id": "neighbor", "kind": "speed", "selector": {"lane_id": "a_branch_0"}, "expected": 8},
        {"id": "removed", "kind": "speed", "selector": {"lane_id": "a_0"}, "expected": 10}]}), encoding="utf-8")
    before = facts.read_bytes()
    plan = {"source": {"path": str(source)}, "operation": {"params": {"edge_id": "a"}}, "lane_index_mapping": {"1": 0}}
    output = tmp_path / "candidate-facts.json"
    record = _remap_lane_count_facts(facts, plan, output)
    rows = json.loads(output.read_text())["facts"]
    assert [row["selector"]["lane_id"] for row in rows] == ["a_0", "a_branch_0", "removed-by-edit:a_0"]
    assert "a_branch_0" not in record["lane_id_mapping"]
    assert facts.read_bytes() == before


def test_direct_stop_line_does_not_guess_the_length_policy_for_multilane_turns():
    from astra_sumo.core.network_edit_operations import _stop_line_patches
    root = ET.fromstring('''<net>
      <edge id="a"><lane id="a_0" index="0" length="10" shape="0,0 10,0"/>
        <lane id="a_1" index="1" length="10" shape="0,3 10,3"/></edge>
      <edge id=":j_0" function="internal"><lane id=":j_0_0" index="0" length="5" shape="10,0 13,0 13,2"/>
        <lane id=":j_0_1" index="1" length="8" shape="10,3 13,3 13,8"/></edge>
      <connection from="a" fromLane="0" to="left" toLane="0" via=":j_0_0" dir="l"/>
      <connection from="a" fromLane="1" to="left" toLane="1" via=":j_0_1" dir="l"/></net>''')
    assert _stop_line_patches(root, {"lane_id": "a_0", "end_offset_m": 2}) is None


@pytest.mark.skipif(not shutil.which("netconvert") or not shutil.which("sumo"), reason="SUMO binaries required")
@pytest.mark.parametrize("junction_z", [0, 20])
def test_direct_stop_line_matches_native_edge_means_and_survives_native_reconversion(tmp_path, junction_z):
    from astra_sumo.core.network_edit_operations import _stop_line_patches, write_direct_lane_edit
    nodes, edges = tmp_path / "nodes.xml", tmp_path / "edges.xml"
    nodes.write_text(f'''<nodes><node id="j" x="0" y="0" z="{junction_z}" type="traffic_light"/>
        <node id="w" x="-200" y="0" z="0"/><node id="e" x="200" y="0" z="0"/>
        <node id="n" x="0" y="200" z="0"/><node id="s" x="0" y="-200" z="0"/></nodes>''', encoding="utf-8")
    edges.write_text('<edges>' + ''.join(f'<edge id="{name}" from="{a}" to="{b}" numLanes="2"/>' for name, a, b in (
        ("WC", "w", "j"), ("CE", "j", "e"), ("EC", "e", "j"), ("CW", "j", "w"),
        ("NC", "n", "j"), ("CS", "j", "s"), ("SC", "s", "j"), ("CN", "j", "n"))) + '</edges>', encoding="utf-8")
    source, direct, native, reloaded = (tmp_path / name for name in ("source.net.xml", "direct.net.xml", "native.net.xml", "reloaded.net.xml"))
    executable = shutil.which("netconvert")
    built = subprocess.run([executable, "-n", str(nodes), "-e", str(edges), "--offset.disable-normalization", "true",
                            "--precision", "12", "-o", str(source)], capture_output=True, text=True, timeout=30)
    assert built.returncode == 0, built.stderr
    source_root = ET.parse(source).getroot()
    params = {"lane_id": "WC_1", "end_offset_m": 5}
    patches = _stop_line_patches(source_root, params)
    assert patches is not None
    write_direct_lane_edit(source, direct, {"operation": "move_stop_line", "params": params})
    offset_file = tmp_path / "offset.xml"
    offset_file.write_text('<edges><edge id="WC"><lane index="1" endOffset="5"/></edge></edges>', encoding="utf-8")
    unrelated = tmp_path / "unrelated.xml"
    unrelated.write_text('<edges><edge id="CS" speed="10"/></edges>', encoding="utf-8")
    for parent, patch, output in ((source, offset_file, native), (direct, unrelated, reloaded)):
        converted = subprocess.run([executable, "-s", str(parent), "-e", str(patch), "--offset.disable-normalization", "true",
                                    "--precision", "12", "-o", str(output)], capture_output=True, text=True, timeout=30)
        assert converted.returncode == 0, converted.stderr
    roots = [ET.parse(path).getroot() for path in (direct, native, reloaded)]
    expected = float(source_root.find("edge[@id='WC']/lane").get("length")) - 2.5
    for root in roots:
        assert [float(lane.get("length")) for lane in root.find("edge[@id='WC']").findall("lane")] == pytest.approx([expected, expected])
    for root in roots[1:]:
        a = roots[0].find("edge/lane[@id='WC_1']").get("shape").split()[-1]
        b = root.find("edge/lane[@id='WC_1']").get("shape").split()[-1]
        assert tuple(map(float, a.split(","))) == pytest.approx(tuple(map(float, b.split(","))), abs=1e-7)
    if junction_z == 0:
        def via(root):
            return next(row.get("via") for row in root.findall("connection")
                        if (row.get("from"), row.get("fromLane"), row.get("to"), row.get("toLane")) == ("WC", "1", "CE", "1"))
        lengths = [float(root.find(f"edge/lane[@id='{via(root)}']").get("length")) for root in roots]
        assert lengths == pytest.approx([lengths[0]] * 3)
    for edge in roots[0].findall("edge"):
        if any(lane.get("id") in patches for lane in edge.findall("lane")):
            assert len({float(lane.get("length")) for lane in edge.findall("lane")}) == 1
    for tag in ("junction", "connection", "tlLogic"):
        assert [ET.tostring(row) for row in roots[0].findall(tag)] == [ET.tostring(row) for row in source_root.findall(tag)]


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


@pytest.mark.parametrize("shape", [
    [[0, 0], [1, 0, 2]], [[0, 0, 0], [1, 0, float("nan")]],
    [[0, 0, 0], [1, 0, True]], [[0, 0, 0], [0, 0, 2]],
])
def test_road_shapes_reject_mixed_dimensions_invalid_heights_and_vertical_roads(shape):
    operation = {"operation": "set_edge_shape", "params": {"edge_id": "road", "shape": shape}}
    assert validate_scoped_operation(operation)


def test_spatial_road_shape_check_does_not_accept_lost_elevation():
    from astra_sumo.core.network_edit_operations import _line_shape, _road_shape_matches

    requested = [[0, 0, 0], [10, 2, 5], [20, 0, 0]]
    assert _line_shape(requested).split()[1] == "10.000000000000,2.000000000000,5.000000000000"
    assert _road_shape_matches(requested, requested)
    assert _road_shape_matches([[0, 0], [10, 2, 5], [20, 0]], requested)
    assert _road_shape_matches(requested, [[str(number) for number in point] for point in requested])
    assert not _road_shape_matches([[x, y, 0] for x, y, _ in requested], requested)
    assert not _road_shape_matches([[x, y] for x, y, _ in requested], requested)


def test_spatial_road_shape_matches_published_schema():
    from jsonschema import Draft202012Validator

    schema = json.loads((Path(__file__).parents[1] / "schemas/product/astra.network-edit-ledger.v1.schema.json").read_text(encoding="utf-8"))
    alternatives = schema["properties"]["operations"]["items"]["allOf"][1]["oneOf"]
    contract = next(row for row in alternatives if row["properties"]["operation"].get("const") == "set_edge_shape")
    validator = Draft202012Validator(contract)
    for shape in ([[0, 0], [1, 1]], [[0, 0, 0], [1, 1, 2]]):
        validator.validate({"operation": "set_edge_shape", "params": {"edge_id": "road", "shape": shape}})
    assert list(validator.iter_errors({"operation": "set_edge_shape", "params": {"edge_id": "road", "shape": [[0, 0], [1, 1, 2]]}}))


@pytest.mark.skipif(not shutil.which("netconvert") or not shutil.which("sumo"), reason="SUMO binaries required")
def test_native_road_shape_edit_preserves_requested_elevation(tmp_path):
    source = _source(tmp_path, "set_edge_shape")
    digest = file_sha256(source)
    shape = [[0, 0, 0], [10, 2, 5], [20, 0, 0]]
    operation = {"id": "spatial-road", "operation": "set_edge_shape", "status": "accepted",
        "params": {"edge_id": "middle", "shape": shape}, "rationale": "Preserve source elevation.",
        "evidence": [{"path": str(source), "sha256": digest}], "rollback": {"action": "discard_candidate"}}
    report = materialize_corridor_edit_variant(output_dir=tmp_path / "candidate",
        ledger={"source_net_file": str(source), "source_sha256": digest, "operations": [operation]},
        netconvert_binary=shutil.which("netconvert"))
    assert report["status"] == "pass", report
    edge = ET.parse(report["candidate_net_file"]).getroot().find("edge[@id='middle']")
    actual = [tuple(map(float, point.split(","))) for point in edge.get("shape").split()]
    assert [point + (0.0,) * (3 - len(point)) for point in actual] == [tuple(point) for point in shape]
    assert max(float(point.split(",")[2]) for lane in edge.findall("lane") for point in lane.get("shape").split()) > 0
    assert file_sha256(source) == digest


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
def test_signal_relocation_cannot_move_only_part_of_a_shared_controller(tmp_path):
    source = _source(tmp_path, "relocate_tls_control", remote_controller="shared")
    operation = {"operation": "relocate_tls_control", "params": {
        "from_node": "A", "to_node": "B", "tls_id": "shared", "link_mapping": [{
            "source": {"from": "in", "fromLane": 0, "to": "middle", "toLane": 0},
            "target": {"from": "middle", "fromLane": 0, "to": "out", "toLane": 0}}]}}
    errors = validate_scoped_operation(operation, ET.parse(source).getroot())
    assert errors and "map every source link" in errors[0]["detail"]


@pytest.mark.skipif(not shutil.which("netconvert"), reason="netconvert required")
@pytest.mark.parametrize("full_input", [False, True])
def test_existing_edge_deletion_uses_the_shared_exterior_protection(tmp_path, monkeypatch, full_input):
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
    if full_input:
        monkeypatch.setattr("astra_sumo.core.local_network_conversion.local_conversion_input",
            lambda *args, **kwargs: (None, {"status": "full_network", "reason": "test_full_input"}))
    def shifted_conversion(command, **kwargs):
        result = run_command(command, **kwargs)
        assert result.returncode == 0, result.stderr
        file = Path(command[command.index("--output-file") + 1])
        changed = ET.parse(file)
        lane = changed.getroot().find("edge[@id='remote']/lane")
        if full_input:
            assert lane is not None
            lane.set("length", "27.84")
            lane.set("shape", "-477.84,0 -450,0")
        else:
            assert lane is None
        changed.write(file, encoding="utf-8", xml_declaration=True)
        return result
    result = materialize_corridor_edit_variant(output_dir=tmp_path / "candidate",
        ledger={"source_net_file": str(source), "source_sha256": digest, "operations": [operation]},
        netconvert_binary=shutil.which("netconvert"), command_runner=shifted_conversion)
    assert result["status"] == "pass", result
    assert result["semantic_validation"]["local_conversion"]["status"] == ("full_network" if full_input else "local")
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
