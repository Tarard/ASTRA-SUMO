import xml.etree.ElementTree as ET
from copy import deepcopy
from pathlib import Path
import shutil
import subprocess

import pytest

from torii_sumo.core.source_movement_support import audit_source_movement_support
from torii_sumo.core import source_movement_support as support
from torii_sumo.core.candidate_contracts import file_sha256


def _edge(root, edge_id, source=None, target=None, lanes=1, allow="delivery"):
    attrs = {"id": edge_id}
    attrs.update({"from": source, "to": target} if source else {"function": "internal"})
    edge = ET.SubElement(root, "edge", attrs)
    for index in range(lanes):
        ET.SubElement(edge, "lane", id=f"{edge_id}_{index}", index=str(index), allow=allow)


def _connection(root, source, target, source_lane=0, target_lane=0, via=None):
    attrs = {"from": source, "to": target, "fromLane": str(source_lane), "toLane": str(target_lane)}
    if via:
        attrs["via"] = via
    ET.SubElement(root, "connection", attrs)


def _networks():
    original, candidate = ET.Element("net"), ET.Element("net")
    for root, owner in ((original, "b"), (candidate, "joined")):
        _edge(root, "in", "entry", "a" if root is original else owner)
        _edge(root, "out", owner, "exit", lanes=3)
        _edge(root, "new_branch", owner, "other_exit")
    _edge(original, "middle", "a", "b")
    _edge(original, ":a_0")
    _edge(original, ":b_0")
    _connection(original, "in", "middle", via=":a_0_0")
    _connection(original, ":a_0", "middle")
    _connection(original, "middle", "out", via=":b_0_0")
    _connection(original, ":b_0", "out")
    # A route through a junction outside the collapsed area is not support.
    _edge(original, "escape", "a", "outside")
    _edge(original, "return", "outside", "b")
    _connection(original, "in", "escape")
    _connection(original, "escape", "return")
    _connection(original, "return", "out", target_lane=1)
    for index, (target, lane) in enumerate((("out", 0), ("out", 1), ("out", 2), ("new_branch", 0))):
        via = f":joined_{index}"
        _edge(candidate, via)
        _connection(candidate, "in", target, target_lane=lane, via=via + "_0")
        _connection(candidate, via, target, target_lane=lane)
    return original, candidate


def _audit(original, candidate, **kwargs):
    return audit_source_movement_support(
        original,
        candidate,
        group_source_node_ids={"joined": ["a", "b"]},
        official_required_pairs=[("in", 0, "out", 2)],
        **kwargs,
    )


def test_support_keeps_source_paths_and_only_proposes_redundant_fanout_removal():
    original, candidate = _networks()
    # Native netconvert can repeat index="0" on internal lanes; connections
    # address lane order, as the shared Connection Mode audit already does.
    ET.SubElement(original.find("edge[@id=':b_0']"), "lane", id=":b_0_1", index="0", allow="delivery")
    before = ET.tostring(original), ET.tostring(candidate)
    report = _audit(original, candidate)
    assert [r["connection"] for r in report["source_backed_extra"]] == [["in", 0, "out", 0]]
    assert report["unsupported_redundant_pairs"] == [["in", 0, "out", 1]]
    assert [r["connection"] for r in report["unresolved"]] == [["in", 0, "new_branch", 0]]
    witness = report["source_backed_extra"][0]["source_paths"][0]
    assert witness["lane_ids"] == ["in_0", ":a_0_0", "middle_0", ":b_0_0", "out_0"]
    assert witness["vehicle_classes"] == ["delivery"]
    assert before == (ET.tostring(original), ET.tostring(candidate))
    protected_alternative = audit_source_movement_support(original, candidate, group_source_node_ids={"joined": ["a", "b"]}, official_required_pairs=[("in", 0, "out", 0), ("in", 0, "out", 2)])
    assert protected_alternative["source_backed_extra"] == []
    assert protected_alternative["unsupported_redundant_pairs"] == [["in", 0, "out", 1]]


def test_permission_and_unknown_lane_identity_cannot_authorize_pruning():
    original, candidate = _networks()
    original.find("edge[@id=':a_0']/lane").set("allow", "bus")
    report = _audit(original, candidate)
    assert report["source_backed_extra"] == []
    assert report["unsupported_redundant_pairs"] == []

    original, candidate = _networks()
    ET.SubElement(candidate.find("edge[@id='in']"), "lane", id="in_1", index="1", allow="delivery")
    assert _audit(original, candidate)["unsupported_redundant_pairs"] == []
    assert _audit(original, candidate, current_lane_to_original_lane={"in_0": "in_0"})["unsupported_redundant_pairs"] == [["in", 0, "out", 1]]
    assert _audit(original, candidate, current_lane_to_original_lane={"in_0": None})["unsupported_redundant_pairs"] == []
    original, candidate = _networks()
    assert _audit(original, candidate, identity_changed_edges=["in"])["unsupported_redundant_pairs"] == []


def test_barmbeker_parking_lane_cases_use_same_exit_lane_support():
    original, candidate = _networks()
    mapping = {"in": "-100495246", "out": "-135514306#0"}
    for root in (original, candidate):
        for edge in root.findall("edge"):
            old = edge.get("id")
            if old in mapping:
                edge.set("id", mapping[old])
                for lane in edge.findall("lane"):
                    lane.set("id", f"{mapping[old]}_{lane.get('index')}")
        for connection in root.findall("connection"):
            for key in ("from", "to"):
                connection.set(key, mapping.get(connection.get(key), connection.get(key)))
    report = audit_source_movement_support(original, candidate, group_source_node_ids={"joined": ["a", "b"]}, official_required_pairs=[("-100495246", 0, "-135514306#0", 2)])
    assert report["unsupported_redundant_pairs"] == [["-100495246", 0, "-135514306#0", 1]]


def _lane_change_network():
    root = ET.Element("net")
    _edge(root, "in", "entry", "a")
    _edge(root, "middle", "a", "b", lanes=2)
    _edge(root, "out", "b", "exit")
    for edge in root.findall("edge"):
        for lane in edge.findall("lane"):
            lane.set("length", "40")
            lane.set("shape", f"0,{lane.get('index')} 40,{lane.get('index')}")
    _connection(root, "in", "middle", target_lane=1)
    _connection(root, "middle", "out")
    return root


def test_static_interior_lane_change_is_only_a_proposal_until_a_source_run_proves_it():
    root = _lane_change_network()
    index = support._index(root)
    assert not support._source_paths(index, "in_0", "out_0", {"a", "b"}, {"delivery"})
    proposal = support._source_lane_change_path(index, "in_0", "out_0", {"a", "b"}, "delivery")
    assert proposal["lane_ids"] == ["in_0", "middle_1", "middle_0", "out_0"]
    assert proposal["lane_changes"][0]["permission_attribute"] == "changeRight"


@pytest.mark.parametrize("fault", ["outside", "forbidden", "different_mode", "reverse_lane"])
def test_static_lane_change_proposal_requires_interior_same_direction_and_permissions(fault):
    root = _lane_change_network()
    edge = root.find("edge[@id='middle']")
    if fault == "outside":
        edge.set("from", "outside")
    elif fault == "forbidden":
        edge.find("lane[@id='middle_1']").set("changeRight", "bus")
    elif fault == "different_mode":
        edge.find("lane[@id='middle_0']").set("allow", "bus")
    else:
        edge.find("lane[@id='middle_0']").set("shape", "40,0 0,0")
    assert support._source_lane_change_path(support._index(root), "in_0", "out_0", {"a", "b"}, "delivery") is None


def _observed_internal_run(tmp_path, fault=None):
    root = _lane_change_network()
    ET.SubElement(root.find("edge[@id='in']"), "lane", id="in_1", index="1", allow="delivery", length="40", shape="0,1 40,1")
    root.remove(root.find("connection[@from='in']"))
    _edge(root, ":a_0", lanes=3)
    _edge(root, ":a_1", lanes=2)
    for edge in root.findall("edge"):
        if edge.get("function") == "internal":
            for lane in edge.findall("lane"):
                lane.set("shape", f"0,{lane.get('index')} 10,{lane.get('index')}")
                lane.set("length", "10")
    _connection(root, "in", "middle", target_lane=1, via=":a_0_1")
    _connection(root, "in", "middle", source_lane=1, via=":a_0_0")
    _connection(root, "in", "middle", via=":a_1_1")
    _connection(root, "in", "middle", source_lane=1, via=":a_1_0")
    _connection(root, ":a_0", "middle", source_lane=1, target_lane=1)
    _connection(root, ":a_0", "middle")
    event = {"id": "probe", "type": "permitted", "time": "3", "from": ":a_0_1", "to": ":a_0_0", "dir": "-1", "reason": "strategic|urgent"}
    members = ["a", "b"]
    if fault == "outside":
        members = ["b"]
    elif fault == "different_internal_turn":
        event["to"] = ":a_1_0"
    elif fault == "nonadjacent":
        event.update({"from": ":a_0_2", "to": ":a_0_0"})
    elif fault == "different_mode":
        root.find("edge[@id=':a_0']/lane[@id=':a_0_0']").set("allow", "bus")
    elif fault == "forbidden":
        root.find("edge[@id=':a_0']/lane[@id=':a_0_1']").set("changeRight", "bus")
    elif fault == "reverse_lane":
        root.find("edge[@id=':a_0']/lane[@id=':a_0_0']").set("shape", "10,0 0,0")
    elif fault == "forced":
        event["reason"] = "traci|urgent"
    elif fault == "wrong_event_time":
        event["time"] = "20"
    elif fault == "event_not_on_route":
        event.update({"from": ":a_1_1", "to": ":a_1_0"})
    elif fault == "wrong_event_type":
        event["type"] = "other"
    elif fault == "same_predecessor":
        root.find("connection[@via=':a_0_0']").set("fromLane", "0")
    elif fault == "unequal_internal_lengths":
        root.find("edge[@id=':a_0']/lane[@id=':a_0_0']").set("length", "9")
    elif fault == "wrong_event_direction":
        event["dir"] = "1"
    routes = ET.fromstring('<routes><vType id="permitted" vClass="delivery"/><vehicle id="probe" type="permitted" depart="0" departLane="0" arrivalLane="0"><route edges="in middle out"/></vehicle></routes>')
    changes = ET.Element("lanechanges")
    ET.SubElement(changes, "change", event)
    fcd = ET.Element("fcd-export")
    for time, lane in [(0, "in_0"), (2, ":a_0_1"), (3, ":a_0_0"), (4, "middle_0"), (8, "out_0")]:
        ET.SubElement(ET.SubElement(fcd, "timestep", time=str(time)), "vehicle", id="probe", lane=lane)
    summary = ET.fromstring('<summary><step arrived="1" collisions="0" teleports="0" running="0" waiting="0" discarded="0"/></summary>')
    tripinfo = ET.fromstring('<tripinfos><tripinfo id="probe" depart="0" arrival="10" vaporized=""/></tripinfos>')
    vehroute = ET.fromstring('<routes><vehicle id="probe" depart="0" arrival="10"><route edges="in :a_0 middle out" exitTimes="2 4 8 10"/></vehicle></routes>')
    if fault == "unfinished":
        summary[0].set("arrived", "0")
        summary[0].set("running", "1")
        tripinfo.clear()
    elif fault == "vaporized":
        tripinfo[0].set("vaporized", "true")
    elif fault == "wrong_vehicle":
        vehroute[0].set("id", "other")
    elif fault == "terminal_time_mismatch":
        tripinfo[0].set("arrival", "11")
    elif fault == "after_horizon":
        tripinfo[0].set("arrival", "61")
        vehroute[0].set("arrival", "61")
    elif fault == "replaced_route":
        vehroute[0][0].set("replacedOnEdge", "middle")
    evidence = {"start_lane": "in_0", "target_lane": "out_0", "source_node_ids": members, "end_time_s": 60}
    record = {"vehicle_class": "delivery", "command": {"returncode": 0}, "artifacts": {}}
    for name, xml in (("routes", routes), ("lanechanges", changes), ("fcd", fcd), ("summary", summary), ("tripinfo", tripinfo), ("vehroute", vehroute)):
        path = tmp_path / f"{name}.xml"
        ET.ElementTree(xml).write(path, encoding="utf-8")
        record["artifacts"][name] = {"path": str(path), "sha256": file_sha256(path)}
    return support._index(root), evidence, record


def test_observed_native_internal_lane_change_can_prove_source_path(tmp_path):
    index, evidence, record = _observed_internal_run(tmp_path)
    before = support._source_lane_change_path(index, "in_0", "out_0", {"a", "b"}, "delivery")
    assert before["lane_changes"][0]["edge_id"] == "middle"
    result = support._inspect_source_lane_change_run(index, evidence, record)
    assert result["status"] == "pass"
    # Observed internal changes do not silently change route production.
    assert support._source_lane_change_path(index, "in_0", "out_0", {"a", "b"}, "delivery") == before


@pytest.mark.parametrize("event_time,exit_times", [
    ("1", "2 4 8 10"),  # Before the vehicle reaches this internal edge.
    ("5", "2 4 8 10"),  # After it leaves, but still within the overall trip.
    ("3", ""),
    ("3", "2 4 10"),
    ("3", "2 nan 8 10"),
    ("3", "2 4 3 10"),
    ("3", "2 4 8 9"),
])
def test_lane_change_time_requires_complete_matching_edge_visit(tmp_path, event_time, exit_times):
    index, evidence, record = _observed_internal_run(tmp_path)
    for name in ("lanechanges", "vehroute"):
        artifact = record["artifacts"][name]
        tree = ET.parse(artifact["path"])
        if name == "lanechanges":
            tree.getroot().find("change").set("time", event_time)
        else:
            tree.getroot().find("vehicle/route").set("exitTimes", exit_times)
        tree.write(artifact["path"], encoding="utf-8")
        artifact["sha256"] = file_sha256(Path(artifact["path"]))
    result = support._inspect_source_lane_change_run(index, evidence, record)
    assert result["status"] == "review_required"


@pytest.mark.parametrize("exit_times", ["3 4 8 10", "2 3 8 10", "3 3 8 10"])
def test_lane_change_at_edge_visit_boundary_does_not_require_internal_fcd_sample(tmp_path, exit_times):
    index, evidence, record = _observed_internal_run(tmp_path)
    for name in ("fcd", "vehroute"):
        artifact = record["artifacts"][name]
        tree = ET.parse(artifact["path"])
        if name == "fcd":
            for step in list(tree.getroot()):
                if step.find("vehicle").get("lane").startswith(":"):
                    tree.getroot().remove(step)
        else:
            tree.getroot().find("vehicle/route").set("exitTimes", exit_times)
        tree.write(artifact["path"], encoding="utf-8")
        artifact["sha256"] = file_sha256(Path(artifact["path"]))
    assert support._inspect_source_lane_change_run(index, evidence, record)["status"] == "pass"


def test_internal_sibling_lanes_from_one_predecessor_are_not_lane_change_support(tmp_path):
    index, _, _ = _observed_internal_run(tmp_path, "same_predecessor")
    assert support._interior_lane_change(index, ":a_0_1", ":a_0_0", {"a", "b"}, "delivery", observed=True) is None


@pytest.mark.parametrize("fault", ["outside", "different_internal_turn", "nonadjacent", "different_mode", "forbidden", "reverse_lane", "forced", "wrong_event_time", "event_not_on_route", "wrong_event_type", "same_predecessor", "unequal_internal_lengths", "wrong_event_direction", "unfinished", "vaporized", "wrong_vehicle", "terminal_time_mismatch", "after_horizon", "replaced_route"])
def test_observed_internal_lane_change_does_not_relax_source_run_guards(tmp_path, fault):
    index, evidence, record = _observed_internal_run(tmp_path, fault)
    assert support._inspect_source_lane_change_run(index, evidence, record)["status"] == "review_required"


@pytest.mark.skipif(not shutil.which("netconvert") or not shutil.which("sumo"), reason="SUMO binaries required")
@pytest.mark.parametrize("never_green", [False, True])
def test_source_probe_uses_finite_horizon_wait_and_requires_normal_completion(tmp_path, never_green):
    nodes, edges, connections, source = [tmp_path / name for name in ("nodes.xml", "edges.xml", "connections.xml", "source.net.xml")]
    nodes.write_text('<nodes><node id="w" x="-60" y="0"/><node id="a" x="0" y="0" type="traffic_light"/><node id="b" x="40" y="0"/><node id="e" x="100" y="0"/></nodes>', encoding="utf-8")
    edges.write_text('<edges><edge id="in" from="w" to="a" allow="delivery"/><edge id="middle" from="a" to="b" numLanes="2" allow="delivery"/><edge id="out" from="b" to="e" allow="delivery"/></edges>', encoding="utf-8")
    connections.write_text('<connections><connection from="in" to="middle" fromLane="0" toLane="1"/><connection from="middle" to="out" fromLane="0" toLane="0"/></connections>', encoding="utf-8")
    subprocess.run(["netconvert", "-n", str(nodes), "-e", str(edges), "-x", str(connections), "-o", str(source)], check=True, capture_output=True, timeout=30)
    tree = ET.parse(source)
    signal = tree.getroot().find("tlLogic[@id='a']")
    for phase in list(signal):
        signal.remove(phase)
    ET.SubElement(signal, "phase", duration="400", state="r")
    ET.SubElement(signal, "phase", duration="40", state="r" if never_green else "G")
    tree.write(source, encoding="utf-8")
    before = source.read_bytes()
    evidence = support.probe_source_interior_lane_changes(source_net=source, start_lane="in_0", target_lane="out_0", source_node_ids=["a", "b"], vehicle_classes=["delivery"], output_dir=tmp_path / "probes", end_time_s=600)
    record = evidence["results"][0]
    command = record["command"]["command"]
    assert float(command[command.index("--time-to-teleport") + 1]) == 600
    assert evidence["time_to_teleport_s"] == 600
    assert evidence["status"] == ("review_required" if never_green else "pass")
    assert record["summary"]["teleports"] == "0"
    assert record["checks"]["completed_without_collision_or_teleport"] is not never_green
    assert source.read_bytes() == before


@pytest.mark.skipif(not shutil.which("netconvert") or not shutil.which("sumo"), reason="SUMO binaries required")
def test_real_source_lane_change_evidence_is_mode_scope_and_hash_bound(tmp_path):
    nodes, edges, connections, source = [tmp_path / name for name in ("nodes.xml", "edges.xml", "connections.xml", "source.net.xml")]
    nodes.write_text('<nodes><node id="w" x="-60" y="0"/><node id="a" x="0" y="0"/><node id="b" x="40" y="0"/><node id="e" x="100" y="0"/></nodes>', encoding="utf-8")
    edges.write_text('<edges><edge id="in" from="w" to="a" allow="passenger delivery"/><edge id="middle" from="a" to="b" numLanes="2" allow="passenger delivery"/><edge id="out" from="b" to="e" allow="passenger delivery"/></edges>', encoding="utf-8")
    connections.write_text('<connections><connection from="in" to="middle" fromLane="0" toLane="1"/><connection from="middle" to="out" fromLane="0" toLane="0"/></connections>', encoding="utf-8")
    subprocess.run(["netconvert", "-n", str(nodes), "-e", str(edges), "-x", str(connections), "-o", str(source)], check=True, capture_output=True, timeout=30)
    original = ET.parse(source).getroot()
    candidate = ET.Element("net")
    _edge(candidate, "in", "w", "joined", allow="passenger delivery")
    _edge(candidate, "out", "joined", "e", allow="passenger delivery")
    _connection(candidate, "in", "out")
    options = {"group_source_node_ids": {"joined": ["a", "b"]}, "official_required_pairs": []}
    assert audit_source_movement_support(original, candidate, **options)["unresolved"]
    evidence = support.probe_source_interior_lane_changes(source_net=source, start_lane="in_0", target_lane="out_0", source_node_ids=["a", "b"], vehicle_classes=["passenger", "delivery"], output_dir=tmp_path / "probes", end_time_s=60)
    assert evidence["status"] == "pass"
    assert len(evidence["results"]) == 2
    assert all(row["observed_lane_changes"][0]["from"] == "middle_1" for row in evidence["results"])
    assert all(row["observed_lane_changes"][0]["to"] == "middle_0" for row in evidence["results"])
    audit = audit_source_movement_support(original, candidate, source_lane_change_evidence=[evidence], **options)
    assert not audit["unresolved"]
    assert audit["source_backed_extra"][0]["supported_vehicle_classes"] == ["delivery", "passenger"]
    assert audit["source_backed_extra"][0]["source_lane_change_evidence"]
    partial = deepcopy(evidence)
    partial["results"] = [row for row in partial["results"] if row["vehicle_class"] == "passenger"]
    assert audit_source_movement_support(original, candidate, source_lane_change_evidence=[partial], **options)["unresolved"]
    mismatched = deepcopy(evidence)
    mismatched["source_node_ids"].append("unrelated")
    assert audit_source_movement_support(original, candidate, source_lane_change_evidence=[mismatched], **options)["unresolved"]
    for fault in ("missing_event", "outside_event", "driving_intervention"):
        invalid = deepcopy(evidence)
        invalid["results"] = [invalid["results"][0]]
        record = invalid["results"][0]
        name = "routes" if fault == "driving_intervention" else "lanechanges"
        replacement = tmp_path / f"{fault}.xml"
        changed = ET.parse(record["artifacts"][name]["path"]).getroot()
        if fault == "missing_event":
            changed.clear()
        elif fault == "outside_event":
            changed.find("change").set("from", "in_0")
        else:
            changed.find("vType").set("lcStrategic", "100")
        ET.ElementTree(changed).write(replacement, encoding="utf-8")
        record["artifacts"][name] = {"path": str(replacement), "sha256": file_sha256(replacement)}
        result = audit_source_movement_support(original, candidate, source_lane_change_evidence=[invalid], **options)
        assert result["source_backed_extra"] == []
    source.write_text(source.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="SHA-256"):
        audit_source_movement_support(original, candidate, source_lane_change_evidence=[evidence], **options)
