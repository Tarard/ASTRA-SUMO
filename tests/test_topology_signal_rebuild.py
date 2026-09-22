from __future__ import annotations

import shutil
import subprocess
import xml.etree.ElementTree as ET

import pytest

from torii_sumo.core.topology_signal_rebuild import (
    build_topology_test_signal_plan,
    rebuild_topology_test_signals,
)


def _network() -> ET.Element:
    root = ET.Element("net", version="1.20")
    edges, junctions, connections = [], [], []
    for number, junction_id in enumerate(("J0", "J1", "OUTSIDE")):
        x = number * 100.0
        tls = "shared-controller" if number < 2 else "outside-controller"
        link_base = number * 2 if number < 2 else 0
        for name, source, target, shape in (
            ("w", "west", junction_id, [(x - 30, 0), (x - 2, 0)]),
            ("s", "south", junction_id, [(x, -30), (x, -2)]),
            ("e", junction_id, "east", [(x + 2, 0), (x + 30, 0)]),
            ("n", junction_id, "north", [(x, 2), (x, 30)]),
        ):
            edge_id = f"{junction_id}-{name}"
            source = source if source == junction_id else f"{junction_id}-{source}"
            target = target if target == junction_id else f"{junction_id}-{target}"
            edge = ET.Element("edge", id=edge_id, **{"from": source, "to": target, "priority": "1"})
            ET.SubElement(edge, "lane", id=f"{edge_id}_0", index="0", speed="10", length="28", width="3.2", shape=" ".join(f"{a},{b}" for a, b in shape))
            edges.append(edge)
            endpoint = source if source != junction_id else target
            point = shape[0] if source != junction_id else shape[-1]
            junctions.append(ET.Element("junction", id=endpoint, type="dead_end", x=str(point[0]), y=str(point[1]), incLanes="" if source != junction_id else f"{edge_id}_0", intLanes="", shape=""))
        junction = ET.Element("junction", id=junction_id, type="traffic_light", x=str(x), y="0", incLanes=f"{junction_id}-w_0 {junction_id}-s_0", intLanes=f":{junction_id}_0_0 :{junction_id}_1_0", shape=f"{x-2},-2 {x+2},-2 {x+2},2 {x-2},2")
        for index, (incoming, outgoing, shape) in enumerate((("w", "e", [(x - 2, 0), (x + 2, 0)]), ("s", "n", [(x, -2), (x, 2)]))):
            internal = ET.Element("edge", id=f":{junction_id}_{index}", function="internal")
            ET.SubElement(internal, "lane", id=f":{junction_id}_{index}_0", index="0", speed="10", length="4", width="3.2", shape=" ".join(f"{a},{b}" for a, b in shape))
            edges.append(internal)
            ET.SubElement(junction, "request", index=str(index), response="00" if index == 0 else "01", foes="10" if index == 0 else "01", cont="0")
            connections.append(ET.Element("connection", **{"from": f"{junction_id}-{incoming}", "to": f"{junction_id}-{outgoing}", "fromLane": "0", "toLane": "0", "via": f":{junction_id}_{index}_0", "tl": tls, "linkIndex": str(link_base + index), "dir": "s", "state": "o"}))
            connections.append(ET.Element("connection", **{"from": f":{junction_id}_{index}", "to": f"{junction_id}-{outgoing}", "fromLane": "0", "toLane": "0", "dir": "s", "state": "M"}))
        junctions.append(junction)
    root.extend(edges)
    for tls, state in (("shared-controller", "GGGG"), ("outside-controller", "Gr")):
        logic = ET.SubElement(root, "tlLogic", id=tls, type="static", programID="0", offset="0")
        ET.SubElement(logic, "phase", duration="15", state=state)
        if tls == "outside-controller":
            ET.SubElement(logic, "phase", duration="15", state="rG")
    root.extend(junctions)
    root.extend(connections)
    return root


def test_plan_follows_shared_controller_and_removes_conflicting_protected_green() -> None:
    plan = build_topology_test_signal_plan(_network(), target_junction_ids=["J0"])
    assert plan["target_tls_ids"] == ["shared-controller"]
    assert plan["expanded_junction_ids"] == ["J0", "J1"]
    assert plan["source_protected_green_conflict_count"] == 2
    controller = plan["controllers"][0]
    green_states = [phase["state"] for phase in controller["phases"] if "G" in phase["state"]]
    assert all(not (state[0] == state[1] == "G" or state[2] == state[3] == "G") for state in green_states)
    assert all(any(state[index] == "G" for state in green_states) for index in range(4))


def test_rebuild_keeps_all_connections_and_outside_program_exactly(tmp_path) -> None:
    root = _network()
    source = tmp_path / "source.net.xml"
    ET.ElementTree(root).write(source, encoding="utf-8", xml_declaration=True)
    report = rebuild_topology_test_signals(net_file=source, output_dir=tmp_path / "rebuilt", target_junction_ids=["J0"])
    candidate = ET.parse(report["candidate_network"]["path"]).getroot()
    assert report["status"] == "pass"
    assert report["signal_role"] == "generic_topology_test"
    assert [row.attrib for row in candidate.findall("connection")] == [row.attrib for row in root.findall("connection")]
    before = root.find("tlLogic[@id='outside-controller']")
    after = candidate.find("tlLogic[@id='outside-controller']")
    assert before.attrib == after.attrib
    assert [phase.attrib for phase in before] == [phase.attrib for phase in after]
    assert report["gates"]["non_target_programs_unchanged"] == "pass"


def test_unrelated_junction_does_not_inherit_unused_red_stages() -> None:
    root = _network()
    for request in root.find("junction[@id='OUTSIDE']").findall("request"):
        request.set("foes", "00")
        request.set("response", "00")
    plan = build_topology_test_signal_plan(root, target_junction_ids=["J0", "OUTSIDE"])
    controllers = {row["tls_id"]: row for row in plan["controllers"]}
    assert controllers["outside-controller"]["phases"] == [{"duration": 15, "state": "GG"}]
    assert len(controllers["shared-controller"]["phases"]) == 6


def test_route_flow_allocation_keeps_conflicts_clearance_and_cycle(tmp_path) -> None:
    routes = tmp_path / "routes.xml"
    routes.write_text('<routes><vehicle id="v" depart="0"><route edges="J0-w J0-e"/></vehicle></routes>', encoding="utf-8")
    equal = build_topology_test_signal_plan(_network(), target_junction_ids=["J0"])
    weighted = build_topology_test_signal_plan(_network(), target_junction_ids=["J0"], route_file=routes)
    before, after = equal["controllers"][0]["phases"], weighted["controllers"][0]["phases"]
    assert [p["state"] for p in before] == [p["state"] for p in after]
    assert sum(p["duration"] for p in before) == sum(p["duration"] for p in after)
    assert [p for p in before if "G" not in p["state"]] == [p for p in after if "G" not in p["state"]]
    assert [p["duration"] for p in after if "G" in p["state"]] == [25, 5]


@pytest.mark.skipif(shutil.which("sumo") is None, reason="SUMO is not installed")
def test_real_sumo_shared_controller_crossing_traffic_finishes_without_collisions(tmp_path) -> None:
    source = tmp_path / "source.net.xml"
    ET.ElementTree(_network()).write(source, encoding="utf-8", xml_declaration=True)
    report = rebuild_topology_test_signals(net_file=source, output_dir=tmp_path / "rebuilt", target_junction_ids=["J0"])
    routes = ET.Element("routes")
    for index, text in enumerate(("J0 w e", "J0 s n", "J1 w e", "J1 s n", "OUTSIDE w e", "OUTSIDE s n")):
        junction, incoming, outgoing = text.split()
        vehicle = ET.SubElement(routes, "vehicle", id=str(index), depart="0")
        ET.SubElement(vehicle, "route", edges=f"{junction}-{incoming} {junction}-{outgoing}")
    route_file = tmp_path / "routes.xml"
    ET.ElementTree(routes).write(route_file, encoding="utf-8", xml_declaration=True)
    summary, collisions = tmp_path / "summary.xml", tmp_path / "collisions.xml"
    run = subprocess.run(["sumo", "-n", report["candidate_network"]["path"], "-r", str(route_file), "--end", "180", "--step-length", "0.1", "--collision.check-junctions", "true", "--collision-output", str(collisions), "--summary-output", str(summary), "--no-step-log", "true"], capture_output=True, text=True)
    assert run.returncode == 0, run.stderr
    assert not ET.parse(collisions).getroot().findall("collision")
    last = ET.parse(summary).getroot().findall("step")[-1]
    assert int(last.get("arrived")) == 6
    assert int(last.get("running")) == int(last.get("waiting")) == 0
    assert "teleport" not in run.stderr.lower()
