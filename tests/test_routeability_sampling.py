import math
import random
import shutil
import subprocess
import xml.etree.ElementTree as ET

import pytest
import sumolib.net
from sumolib import checkBinary

from astra_sumo.core import routeability_audit as audit


pytestmark = pytest.mark.skipif(not shutil.which("netconvert"), reason="SUMO required")


def _routing_model(path, variant):
    nodes, edges = ET.Element("nodes"), ET.Element("edges")
    size = 12
    for i in range(size):
        end = (i + 1) % size if variant != "separate" else (i // 6) * 6 + (i + 1) % 6
        group, position, count = (i // 6, i % 6, 6) if variant == "separate" else (0, i, size)
        angle = 2 * math.pi * position / count
        ET.SubElement(nodes, "node", id=f"n{i}", type="priority",
                      x=str(group * 1000 + 100 * math.cos(angle)), y=str(100 * math.sin(angle)))
        ET.SubElement(edges, "edge", id=f"e{i}", allow="passenger bus", **{"from": f"n{i}", "to": f"n{end}"})
    node_file, edge_file = path.with_suffix(".nodes.xml"), path.with_suffix(".edges.xml")
    ET.ElementTree(nodes).write(node_file, encoding="utf-8", xml_declaration=True)
    ET.ElementTree(edges).write(edge_file, encoding="utf-8", xml_declaration=True)
    result = subprocess.run([checkBinary("netconvert"), "-n", str(node_file), "-e", str(edge_file),
        "--geometry.remove", "false", "--roundabouts.guess", "false", "-o", str(path)],
        capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    if variant == "restricted":
        tree = ET.parse(path)
        turn = tree.getroot().find("connection[@from='e0'][@to='e1']")
        turn.set("allow", "bus")
        tree.getroot().find(f"edge/lane[@id='{turn.get('via')}']").set("allow", "bus")
        tree.write(path, encoding="utf-8", xml_declaration=True)
    return audit.build_routeability_od_policy([path])


def _eager_reference(network, policy, count, seed):
    net = sumolib.net.readNet(str(network), maxcache=1)
    destinations = set(policy["destination_edge_ids"])
    choices = {}
    for source in policy["source_edge_ids"]:
        start = net.getEdge(source)
        reachable = {edge.getID() for edge in net.getReachable(start, vclass="passenger")} & destinations - {source}
        choices[source] = [target for target in sorted(reachable)
                           if net.getShortestPath(start, net.getEdge(target), vClass="passenger")[0] is not None]
    origins = sorted(source for source, targets in choices.items() if targets)
    generator = random.Random(seed)
    root = ET.Element("routes")
    for number in range(count):
        source = generator.choice(origins)
        ET.SubElement(root, "trip", id=str(number), depart=str(number),
                      **{"from": source, "to": generator.choice(choices[source])})
    ET.indent(root)
    return ET.tostring(root, encoding="unicode", xml_declaration=True).encode("utf-8"), choices


@pytest.mark.parametrize("variant", ["cycle", "separate", "restricted"])
@pytest.mark.parametrize("seed", [0, 1, 104, 105, 9999])
def test_sampling_preserves_v1_requests_and_all_pair_counts(tmp_path, variant, seed):
    source = tmp_path / "routing.net.xml"
    policy = _routing_model(source, variant)
    policy["destination_edge_ids"].append("unknown-destination")
    expected, choices = _eager_reference(source, policy, 100, seed)
    output = tmp_path / "trips.xml"
    report = audit._write_source_reachable_trips(source, policy, output, vehicle_count=100, seed=seed)
    assert output.read_bytes() == expected
    assert report["reachable_od_pair_count"] == sum(map(len, choices.values()))
    assert report["reachable_destination_count_by_source"] == {key: len(value) for key, value in choices.items()}
    assert report["sources_without_reachable_destinations"] == sorted(key for key, value in choices.items() if not value)
    usable = set().union(*(set(value) for value in choices.values()))
    assert report["destinations_without_reachable_sources"] == sorted(set(policy["destination_edge_ids"]) - usable)


def test_route_generation_does_not_search_a_path_for_every_possible_pair(tmp_path, monkeypatch):
    source = tmp_path / "routing.net.xml"
    policy = _routing_model(source, "cycle")
    from astra_sumo.core import local_network_conversion
    original = local_network_conversion.loaded_turn_permissions
    calls = []
    def counted(root):
        calls.append(root)
        return original(root)
    monkeypatch.setattr(local_network_conversion, "loaded_turn_permissions", counted)
    audit._write_source_reachable_trips(source, policy, tmp_path / "trips.xml", vehicle_count=100, seed=104)
    assert len(calls) == 1


@pytest.mark.skipif(not shutil.which("duarouter"), reason="SUMO router required")
@pytest.mark.parametrize("restriction,expected_pairs,expected_targets", [("directive", 132, 11), ("internal_lane", 66, 0)])
def test_source_sampling_matches_native_loaded_turn_access(tmp_path, restriction, expected_pairs, expected_targets):
    source = tmp_path / "routing.net.xml"
    policy = _routing_model(source, "cycle")
    tree = ET.parse(source)
    turn = tree.getroot().find("connection[@from='e0'][@to='e1']")
    if restriction == "directive":
        turn.set("allow", "bus")
    else:
        tree.getroot().find(f"edge/lane[@id='{turn.get('via')}']").set("allow", "bus")
    tree.write(source, encoding="utf-8", xml_declaration=True)
    trips = tmp_path / "native.trips.xml"
    trips.write_text('<routes><trip id="probe" depart="0" from="e0" to="e1"/></routes>', encoding="utf-8")
    routed = tmp_path / "native.routes.xml"
    result = subprocess.run([checkBinary("duarouter"), "-n", str(source), "-r", str(trips),
        "-o", str(routed), "--ignore-errors", "true", "--no-step-log", "true"],
        capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    assert len(ET.parse(routed).getroot().findall("vehicle")) == (restriction == "directive")
    report = audit._write_source_reachable_trips(source, policy, tmp_path / "sampled.trips.xml", vehicle_count=100, seed=104)
    assert report["reachable_od_pair_count"] == expected_pairs
    assert report["reachable_destination_count_by_source"]["e0"] == expected_targets
    assert report["method"].endswith("/v2")
