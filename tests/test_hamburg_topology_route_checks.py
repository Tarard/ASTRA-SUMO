import importlib
import json
import shutil
import subprocess
import xml.etree.ElementTree as ET

import pytest

from torii_sumo.core.candidate_contracts import file_sha256


def _network(path, *, blocked=False, missing_start=False, internal_blocked=False, moved_start=False):
    root = ET.Element("net", version="1.27")
    nodes = {"south": (0, 0), "a": (0, 100), "b": (0, 200), "north": (0, 300)}
    edges = [("f0", "south", "a"), ("f1", "a", "b"), ("f2", "b", "north"),
             ("r2", "north", "b"), ("r1", "b", "a"), ("r0", "a", "south")]
    if missing_start:
        edges = [row for row in edges if row[0] != "f0"]
    for name, start, end in edges:
        edge = ET.SubElement(root, "edge", id=name, **{"from": start, "to": end, "name": "Main"})
        shape = " ".join(f"{x},{y}" for x, y in (nodes[start], nodes[end]))
        if moved_start and name == "f0":
            shape = "0,40 0,100"
        ET.SubElement(edge, "lane", id=f"{name}_0", index="0", speed="13.89", length="100", shape=shape)
    if internal_blocked:
        edge = ET.SubElement(root, "edge", id=":b_0", function="internal")
        ET.SubElement(edge, "lane", id=":b_0_0", index="0", speed="13.89", length="1", shape="0,200 0,201", allow="bus")
    for name, (x, y) in nodes.items():
        ET.SubElement(root, "junction", id=name, type="priority", x=str(x), y=str(y),
                      incLanes=" ".join(f"{e}_0" for e, _, to in edges if to == name), intLanes="")
    for start, end in (("f0", "f1"), ("f1", "f2"), ("r2", "r1"), ("r1", "r0")):
        if missing_start and start == "f0":
            continue
        attrs = {"from": start, "to": end, "fromLane": "0", "toLane": "0", "dir": "s", "state": "M"}
        if blocked and start == "f1":
            attrs["allow"] = "bus"
        if internal_blocked and start == "f1":
            attrs["via"] = ":b_0_0"
        ET.SubElement(root, "connection", **attrs)
    if internal_blocked:
        ET.SubElement(root, "connection", **{"from": ":b_0", "to": "f2", "fromLane": "0", "toLane": "0", "dir": "s", "state": "M"})
    ET.ElementTree(root).write(path, encoding="utf-8", xml_declaration=True)


def _manifest(tmp_path, **candidate_options):
    source, candidate = tmp_path / "source.net.xml", tmp_path / "candidate.net.xml"
    _network(source)
    _network(candidate, **candidate_options)
    result = {"schema": "torii.hamburg-aerial-corridor-candidate/v1",
              "inputs": {"source_net": {"path": str(source), "sha256": file_sha256(source)}},
              "artifacts": {"network": {"path": str(candidate), "sha256": file_sha256(candidate)}},
              "physical_parts": [{"node_id": "1", "join_id": "a", "source_node_ids": ["a"]},
                                 {"node_id": "2", "join_id": "b", "source_node_ids": ["b"]}],
              "approach_rebuild": {"approaches": []}, "official_connection_audit": {"required": []}}
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(result), encoding="utf-8")
    return path


def _add_outer_branch(path, *, connected=False, tied=False, remove_original_exit=False):
    root = ET.parse(path).getroot()
    for name, x, y in (("branch", 10, 310), ("far", 10, 300 if tied else 400), ("inner", 0, 250)):
        ET.SubElement(root, "junction", id=name, type="priority", x=str(x), y=str(y), incLanes="", intLanes="")
    start = "b" if connected else "branch"
    for name, first, last, shape in (("outer-exit", start, "far", f"{'0,200' if connected else '10,310'} 10,{300 if tied else 400}"),
                                    ("inner-exit", "b", "inner", "0,200 0,250")):
        edge = ET.SubElement(root, "edge", id=name, **{"from": first, "to": last, "name": "Main"})
        ET.SubElement(edge, "lane", id=f"{name}_0", index="0", speed="13.89", length="100", shape=shape)
        if name == "inner-exit" or connected:
            ET.SubElement(root, "connection", **{"from": "f1", "to": name, "fromLane": "0", "toLane": "0", "dir": "s", "state": "M"})
    if remove_original_exit:
        for element in list(root):
            if element.tag == "edge" and element.get("id") == "f2" or element.tag == "connection" and element.get("to") == "f2":
                root.remove(element)
        root.find("junction[@id='north']").set("incLanes", "")
    ET.ElementTree(root).write(path, encoding="utf-8", xml_declaration=True)


@pytest.mark.parametrize("candidate_loses_exit", [False, True])
def test_source_reachability_excludes_disconnected_outer_branch_without_candidate_reselection(tmp_path, monkeypatch, candidate_loses_exit):
    module = importlib.import_module("torii_sumo.core.hamburg_topology_route_checks")
    path = _manifest(tmp_path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    for role, record in (("source", payload["inputs"]["source_net"]), ("candidate", payload["artifacts"]["network"])):
        network_path = tmp_path / f"{role}.net.xml"
        _add_outer_branch(network_path, remove_original_exit=role == "candidate" and candidate_loses_exit)
        record["sha256"] = file_sha256(network_path)
    path.write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setattr(module, "_run_probe", lambda *args, **kwargs: {"status": "pass"})
    result = module.run_hamburg_topology_route_checks(path, tmp_path / "checks", road_names=["Main"], ordered_node_ids=["1", "2"])
    forward = result["mainline"]["directions"][0]
    assert forward["endpoints"]["to_edge"] == "f2"
    assert any(row["to_edge"] == "outer-exit" and row["reason"] == "no_permitted_named_path_through_selected_order"
               for row in forward["endpoints"]["excluded_source_pairs"])
    assert forward["source"]["route"] == ["f0", "f1", "f2"]
    assert (forward["candidate"]["status"] == "pass") is (not candidate_loses_exit)
    # The candidate still has inner-exit, but it must not replace the frozen f2 endpoint.
    assert forward["endpoints"]["to_edge"] != "inner-exit"


def test_source_selection_finishes_before_candidate_network_is_read(tmp_path, monkeypatch):
    module = importlib.import_module("torii_sumo.core.hamburg_topology_route_checks")
    path = _manifest(tmp_path)
    original_network, original_select = module._network, module._source_endpoints
    reads, selections = [], []

    def network(net_path):
        reads.append(net_path.name)
        return original_network(net_path)

    def select(*args):
        assert "candidate.net.xml" not in reads
        selections.append(args[-1])
        return original_select(*args)

    monkeypatch.setattr(module, "_network", network)
    monkeypatch.setattr(module, "_source_endpoints", select)
    monkeypatch.setattr(module, "_run_probe", lambda *args, **kwargs: {"status": "pass"})
    module.run_hamburg_topology_route_checks(path, tmp_path / "checks", road_names=["Main"], ordered_node_ids=["1", "2"])
    assert selections == [["1", "2"], ["2", "1"]]


def test_equally_outer_reachable_endpoint_pairs_require_review(tmp_path):
    module = importlib.import_module("torii_sumo.core.hamburg_topology_route_checks")
    source = tmp_path / "source.net.xml"
    _network(source)
    _add_outer_branch(source, connected=True, tied=True)
    network = module._network(source)
    result = module._source_endpoints(network, module._named_edges(network, {"Main"}), {"1": {"a"}, "2": {"b"}}, ["1", "2"])
    assert result["status"] == "review_required"
    assert result["reason"] == "equally_outer_source_endpoint_pairs"
    assert {row["to_edge"] for row in result["equally_ranked_pairs"]} == {"f2", "outer-exit"}


@pytest.mark.parametrize("candidate_options", [{}, {"blocked": True}, {"missing_start": True}, {"internal_blocked": True}, {"moved_start": True}])
def test_mainline_uses_source_endpoints_on_a_north_south_corridor(tmp_path, monkeypatch, candidate_options):
    module = importlib.import_module("torii_sumo.core.hamburg_topology_route_checks")
    path = _manifest(tmp_path, **candidate_options)
    monkeypatch.setattr(module, "_run_probe", lambda *args, **kwargs: {"status": "pass"})
    result = module.run_hamburg_topology_route_checks(path, tmp_path / "checks", road_names=["Main"], ordered_node_ids=["1", "2"])
    forward, reverse = result["mainline"]["directions"]
    assert forward["endpoints"]["from_edge"] == "f0"
    assert forward["endpoints"]["to_edge"] == "f2"
    assert reverse["endpoints"]["from_edge"] == "r2"
    assert reverse["endpoints"]["to_edge"] == "r0"
    assert forward["source"]["status"] == "pass"
    assert (forward["candidate"]["status"] == "pass") is (not candidate_options)
    assert result["pocket_access"]["status"] == "not_applicable"
    assert result["inputs_unchanged"]


def test_source_runtime_failure_is_retained_without_changing_fixed_endpoints(tmp_path, monkeypatch):
    module = importlib.import_module("torii_sumo.core.hamburg_topology_route_checks")
    path = _manifest(tmp_path)
    monkeypatch.setattr(module, "_run_probe", lambda network, *args, **kwargs: {"status": "review_required" if network["path"].name.startswith("source") else "pass"})
    result = module.run_hamburg_topology_route_checks(path, tmp_path / "checks", road_names=["Main"], ordered_node_ids=["1", "2"])
    assert result["mainline"]["status"] == "pass"
    forward = result["mainline"]["directions"][0]
    assert forward["endpoints"]["from_edge"] == "f0"
    assert forward["source"]["path_status"] == "pass"
    assert forward["source"]["probe"]["status"] == "review_required"
    assert forward["candidate"]["status"] == "pass"


def test_hash_mismatch_rejects_before_creating_outputs(tmp_path):
    module = importlib.import_module("torii_sumo.core.hamburg_topology_route_checks")
    path = _manifest(tmp_path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["artifacts"]["network"]["sha256"] = "0" * 64
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="SHA-256"):
        module.run_hamburg_topology_route_checks(path, tmp_path / "checks", road_names=["Main"], ordered_node_ids=["1", "2"])
    assert not (tmp_path / "checks").exists()


@pytest.mark.skipif(not shutil.which("sumo") or not shutil.which("netconvert"), reason="SUMO required")
def test_bus_enters_a_new_pocket_from_upstream_with_native_lane_change(tmp_path):
    module = importlib.import_module("torii_sumo.core.hamburg_topology_route_checks")
    nodes = tmp_path / "nodes.xml"
    nodes.write_text('<nodes><node id="w" x="0" y="0"/><node id="m" x="80" y="0"/>'
                     '<node id="j" x="160" y="0"/><node id="e" x="240" y="0"/></nodes>', encoding="utf-8")
    networks = {}
    for role, count in (("source", 2), ("candidate", 3)):
        edges, connections, net = (tmp_path / f"{role}.{suffix}" for suffix in ("edg.xml", "con.xml", "net.xml"))
        edges.write_text(f'<edges><edge id="up" from="w" to="m" numLanes="2" name="Main"/>'
                         f'<edge id="pocket" from="m" to="j" numLanes="{count}" name="Main">'
                         + ('<lane index="0" allow="bus"/>' if count == 3 else '')
                         + '</edge><edge id="out" from="j" to="e" numLanes="2" name="Main"/></edges>', encoding="utf-8")
        offset = count - 2
        connections.write_text('<connections>'
                               f'<connection from="up" to="pocket" fromLane="0" toLane="{offset}"/>'
                               f'<connection from="up" to="pocket" fromLane="1" toLane="{offset + 1}"/>'
                               + ''.join(f'<connection from="pocket" to="out" fromLane="{lane}" toLane="{min(lane, 1)}"/>' for lane in range(count))
                               + '</connections>', encoding="utf-8")
        subprocess.run(["netconvert", "-n", str(nodes), "-e", str(edges), "-x", str(connections), "-o", str(net)],
                       check=True, capture_output=True, timeout=30)
        networks[role] = {"path": str(net), "sha256": file_sha256(net)}
    manifest = {"schema": "torii.hamburg-aerial-corridor-candidate/v1",
                "inputs": {"source_net": networks["source"]}, "artifacts": {"network": networks["candidate"]},
                "physical_parts": [{"node_id": "1", "join_id": "m", "source_node_ids": ["m"]},
                                   {"node_id": "2", "join_id": "j", "source_node_ids": ["j"]}],
                "approach_rebuild": {"approaches": [{"node_id": "2", "intersection_part": "0", "approach": "1",
                    "section_index": 0, "pocket_lane_ids": ["10"], "chain_edge_ids": ["pocket"],
                    "upstream_edge_id": None, "downstream_edge_id": "pocket", "official_lane_indices": {"10": 0},
                    "source_lane_to_downstream_lane": {"0": 1, "1": 2},
                    "lane_attribute_inheritance": {"10": {"source_lane_index": 0}}}]},
                "official_connection_audit": {"required": [{"node_id": "2", "intersection_part": "0",
                    "ingress_lane_id": "10", "sumo_connection": ["pocket", 0, "out", 0], "allowed_vehicle_classes": ["bus"]}]}}
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    result = module.run_hamburg_topology_route_checks(path, tmp_path / "checks", road_names=["Main"], ordered_node_ids=["1", "2"], end_time_s=60)
    pocket = result["pocket_access"]["results"][0]
    assert pocket["status"] == "pass"
    assert pocket["route"][0] == "up"
    assert pocket["native_lane_change_observed"]
    assert pocket["inspection"]["summary"]["arrived"] == 1
    assert pocket["vehicle_routes"]["status"] == "pass"
    assert result["mainline"]["directions"][0]["status"] == "pass"
    assert result["mainline"]["status"] == "review_required"  # No reverse road exists.
