import gzip
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET

import pytest

from astra_sumo.core.candidate_contracts import file_sha256
from astra_sumo.core.road_hierarchy import build_road_hierarchy_variant, select_road_hierarchy
from astra_sumo.core.road_scope import road_hierarchy_tier


@pytest.mark.parametrize("kind,tier", [("highway.primary", "major"), ("highway.secondary_link", "major"),
    ("highway.tertiary", "collector"), ("highway.tertiary_link", "collector"),
    ("highway.residential", "local"), ("highway.unclassified", "local"),
    ("highway.service", "local"), ("custom", "unknown"), ("highway.road", "unknown"),
    ("highway.service|highway.primary", "major")])
def test_coarse_road_tier_uses_source_type(kind, tier):
    assert road_hierarchy_tier(kind) == tier


def test_frozen_ring1_scope_files_match_manifest():
    directory = Path(__file__).parents[1] / "benchmarks/hamburg_ring1_road_scope_20261020_v1"
    fixture = json.loads((directory / "fixture.json").read_text(encoding="utf-8"))
    for name, identity in fixture["files"].items():
        path = directory / name
        assert file_sha256(path) == identity["sha256"]
        assert path.stat().st_size == identity["bytes"]
    native_xml = gzip.decompress((directory / "native-import.net.xml.gz").read_bytes()).decode("utf-8")
    assert hashlib.sha256(ET.canonicalize(native_xml).encode("utf-8")).hexdigest() == fixture["native_network_content_sha256"]


def test_frozen_official_approach_anchors_reference_existing_source_lanes():
    directory = Path(__file__).parents[1] / "benchmarks/hamburg_ring1_road_scope_20261020_v1"
    fixture = json.loads((directory / "fixture.json").read_text(encoding="utf-8"))
    anchors = json.loads((directory / fixture["official_approach_anchors_file"]).read_text(encoding="utf-8"))
    root = ET.fromstring(gzip.decompress((directory / "native-import.net.xml.gz").read_bytes()))
    lanes = {lane.get("id"): edge.get("id") for edge in root.findall("edge") for lane in edge.findall("lane")}
    assert all(lanes[row["lane_id"]] == row["edge_id"] for row in anchors["anchors"])
    assert set(anchors["protected_edge_ids"]) == {row["edge_id"] for row in anchors["anchors"]}
    assert sum(row["reference_motor_lanes"] for row in anchors["motor_lane_counts"].values()) == 149
    assert sum(row["unmapped_source_lanes"] for row in anchors["motor_lane_counts"].values()) == 14
    assert sum(row["source_lateral_offset_unavailable"] for row in anchors["motor_lane_counts"].values()) == 7


@pytest.mark.skipif(not shutil.which("netconvert") or not shutil.which("sumo"), reason="SUMO binaries required")
def test_native_ring1_official_approach_demo(tmp_path):
    repo = Path(__file__).parents[1]
    output = tmp_path / "case"
    run = subprocess.run([sys.executable, str(repo / "examples/06_hamburg_ring1_demo/run_road_selection.py"), str(output)],
        cwd=repo, capture_output=True, text=True, timeout=180)
    assert run.returncode == 0, run.stderr + run.stdout
    case = json.loads((output / "case.json").read_text(encoding="utf-8"))
    assert case["selected_road_count"] == 467
    assert all(case["reproduction"]["checks"].values())
    assert case["official_approach_protection"]["source_content_matches"]
    assert case["official_approach_protection"]["protected_edge_count"] == 130
    assert case["review"]["signal_changes_by_scope"]["changed_retained"] == 0
    report = json.loads(Path(case["road_selection_report"]).read_text(encoding="utf-8"))
    assert all(report["checks"].values())
    assert report["retained_local_turn_changes"] == {"lost": [], "added": []}


def _network():
    root = ET.Element("net", version="1.20")
    definitions = [("main-a", "w", "a", "primary"), ("connector", "a", "b", "tertiary"),
        ("main-b", "b", "e", "primary"), ("side-1", "a", "c", "residential"),
        ("side-2", "c", "d", "service"), ("side-3", "d", "f", "service"),
        ("detector-road", "x", "y", "tertiary")]
    for identity, first, last, kind in definitions:
        edge = ET.SubElement(root, "edge", id=identity, **{"from": first, "to": last}, type=f"highway.{kind}", priority="99")
        ET.SubElement(edge, "lane", id=identity + "_0", index="0", allow="passenger", length="20", shape="0,0 20,0")
        for node in (first, last):
            if root.find(f"junction[@id='{node}']") is None:
                ET.SubElement(root, "junction", id=node, x="0", y="0")
    for first, last in [("main-a", "connector"), ("connector", "main-b"),
                        ("main-a", "side-1"), ("side-1", "side-2"), ("side-2", "side-3")]:
        ET.SubElement(root, "connection", **{"from": first, "to": last}, fromLane="0", toLane="0")
    return root


def test_hierarchy_keeps_detector_road_and_approaches_without_return_loops():
    root = _network()
    before = ET.tostring(root)
    report = select_road_hierarchy(root, protected_edge_ids=["detector-road"])
    assert "protected_road" in next(row for row in report["roads"] if row["edge_id"] == "detector-road")["reasons"]
    assert "connector" in report["selected_edge_ids"]
    assert "side-1" in report["selected_edge_ids"]
    assert report["excluded_edge_ids"] == ["side-2", "side-3"]
    assert not any(row["from"] == "main-b" and row["to"] == "main-a" for row in report["movements"])
    assert any(row["from"] == "main-a" and row["to"] == "main-b" for row in report["movements"])
    assert any(row["cut_roads"] == ["side-2"] and row["exit_edges"] == ["side-1"] for row in report["boundaries"])
    assert ET.tostring(root) == before
    assert all(row.get("priority") == "99" for row in root.findall("edge"))


@pytest.mark.parametrize("turnaround", [False, True])
def test_missing_directed_connection_is_repaired_from_source_only(turnaround):
    root = _network()
    edge = root.find("edge[@id='connector']")
    edge.set("to", "middle")
    intermediate = ET.SubElement(root, "edge", id="middle", **{"from": "middle", "to": "last"}, type="highway.service")
    ET.SubElement(intermediate, "lane", id="middle_0", index="0", length="10", allow="passenger")
    root.find("connection[@from='connector']").set("to", "middle")
    last = ET.SubElement(root, "edge", id="last", **{"from": "last", "to": "b"}, type="highway.service")
    ET.SubElement(last, "lane", id="last_0", index="0", length="10", allow="passenger")
    ET.SubElement(root, "connection", **{"from": "middle", "to": "last"}, fromLane="0", toLane="0")
    ET.SubElement(root, "connection", **{"from": "last", "to": "main-b"}, fromLane="0", toLane="0")
    if turnaround:
        root.find("connection[@from='connector']").set("dir", "t")
        alternative = ET.SubElement(root, "edge", id="alternative", **{"from": "middle", "to": "last"}, type="highway.service")
        ET.SubElement(alternative, "lane", id="alternative_0", index="0", length="100", allow="passenger")
        for first, target in [("connector", "alternative"), ("alternative", "last")]:
            ET.SubElement(root, "connection", **{"from": first, "to": target}, fromLane="0", toLane="0", dir="s")
    # Explicit entry/exit scope does not demand the reverse trip on a one-way corridor.
    report = select_road_hierarchy(root, required_movements=[("main-a", "main-b")])
    assert report["connection_repairs"]
    assert report["connection_repairs"][0]["source_path"] == ["main-a", "connector", "middle", "last", "main-b"]
    assert report["movements"][0]["detour_ratio"] == 1
    assert report["movements"][0]["added_length_m"] == 0
    assert report["connection_repairs"][0]["source_shortest_path_has_turnaround"] == turnaround
    assert report["connection_repairs"][0]["source_path_without_turnaround_exists"]


def test_signal_removal_due_to_pruning_is_separate_from_retained_control_changes():
    from astra_sumo.core.road_hierarchy import _signal_scope_changes
    source = ET.fromstring('''<net>
      <edge id="a" to="removed"/><edge id="b" to="dead"/><edge id="c" to="retained"/>
      <edge id="d" to="changed"/>
      <connection from="a" tl="gone"/><connection from="b" tl="dead-tls"/>
      <connection from="c" tl="retained-tls"/><connection from="d" tl="changed-tls"/>
      </net>''')
    candidate = ET.fromstring('''<net><junction id="dead" type="dead_end"/>
      <junction id="retained" type="priority"/><junction id="changed" type="traffic_light"/></net>''')
    changes = {key: {"source": {"program": "0"}, "candidate": None} for key in ["gone", "dead-tls", "retained-tls", "unknown"]}
    changes["changed-tls"] = {"source": {"program": "0"}, "candidate": {"program": "1"}}
    changes["new"] = {"source": None, "candidate": {"program": "0"}}
    groups = _signal_scope_changes(source, candidate, {"unexpected_changes": changes})
    assert groups == {"removed_with_junction": ["gone"], "removed_at_dead_end": ["dead-tls"],
        "changed_retained": ["changed-tls"], "added": ["new"], "removed_at_retained_junction": ["retained-tls"],
        "unresolved_removed_owner": ["unknown"]}


def test_terminal_side_approaches_do_not_require_an_outside_residential_route():
    root = _network()
    inlet = ET.SubElement(root, "edge", id="side-in", **{"from": "f", "to": "b"}, type="highway.residential")
    ET.SubElement(inlet, "lane", id="side-in_0", index="0", allow="passenger", length="20")
    for first, last in [("side-3", "side-in"), ("side-in", "main-b")]:
        ET.SubElement(root, "connection", **{"from": first, "to": last}, fromLane="0", toLane="0")
    report = select_road_hierarchy(root)
    assert report["excluded_edge_ids"] == ["detector-road", "side-2", "side-3"]
    assert not report["connection_repairs"]
    pairs = {(row["from"], row["to"]) for row in report["movements"]}
    assert {("main-a", "side-1"), ("side-in", "main-b"), ("main-a", "main-b")} <= pairs
    assert ("side-1", "side-in") not in pairs
    explicit = select_road_hierarchy(root, required_movements=[("side-1", "side-in")])
    assert {"side-2", "side-3"} <= set(explicit["selected_edge_ids"])
    assert explicit["connection_repairs"]


def test_unknown_types_remain_visible_and_missing_protected_roads_fail():
    root = _network()
    root.find("edge[@id='side-3']").set("type", "custom.unknown")
    report = select_road_hierarchy(root)
    assert next(row for row in report["roads"] if row["edge_id"] == "side-3")["tier"] == "unknown"
    assert "side-3" in report["selected_edge_ids"]
    with pytest.raises(ValueError, match="protected"):
        select_road_hierarchy(root, protected_way_ids=["999999"])


def test_protection_reasons_keep_study_ways_separate_from_tentative_approaches():
    root = _network()
    root.find("edge[@id='detector-road']").set("id", "123")
    root.find("edge[@id='123']/lane").set("id", "123_0")
    inputs = {"protected_way_ids": ["123"], "protected_edge_ids": ["connector"]}
    original = select_road_hierarchy(root, **inputs)
    annotated = select_road_hierarchy(root, **inputs, protected_edge_evidence={"connector": {
        "basis": "official_motor_approach_correspondence", "mapping_confidences": ["conflict"]}})
    assert annotated["selected_edge_ids"] == original["selected_edge_ids"]
    rows = {row["edge_id"]: row for row in annotated["roads"]}
    assert rows["123"]["protection_evidence"] == [{"basis": "declared_OSM_way", "way_id": "123"}]
    assert rows["connector"]["protection_evidence"][0]["mapping_confidences"] == ["conflict"]
    with pytest.raises(ValueError, match="explicitly protected"):
        select_road_hierarchy(root, **inputs, protected_edge_evidence={"main-b": {"basis": "unbound"}})


@pytest.mark.parametrize("empty", [False, True])
def test_no_effect_does_not_rebuild_or_invent_a_candidate(tmp_path, monkeypatch, empty):
    from astra_sumo.core import road_hierarchy
    root = _network()
    if empty:
        for edge in root.findall("edge"):
            edge.set("type", "highway.service")
    source = tmp_path / "source.net.xml"
    ET.ElementTree(root).write(source, encoding="utf-8")
    def forbidden(*args, **kwargs):
        raise AssertionError("No conversion is needed.")
    monkeypatch.setattr(road_hierarchy, "run_command", forbidden)
    report = build_road_hierarchy_variant(net_file=source, output_dir=tmp_path / "out", keep_tier="major" if empty else "local")
    assert report["status"] == "no_effect" and report["native_conversion_count"] == 0
    assert "candidate" not in report and not (tmp_path / "out/selected.net.xml").exists()


@pytest.mark.parametrize("status,returncode", [("fail", 1), ("fail", 0), ("pass", 0)])
def test_failed_native_attempts_keep_partial_files_without_a_candidate(tmp_path, monkeypatch, status, returncode):
    from astra_sumo.core import road_hierarchy
    from astra_sumo.core.command_runner import CommandResult
    source = tmp_path / "source.net.xml"
    ET.ElementTree(_network()).write(source, encoding="utf-8")
    digest = file_sha256(source)
    def fail(command, **kwargs):
        Path(command[command.index("--output-file") + 1]).write_text("<net", encoding="utf-8")
        return CommandResult(command=command, cwd=str(kwargs["cwd"]), status=status, returncode=returncode, stderr="Native failure")
    monkeypatch.setattr(road_hierarchy, "run_command", fail)
    report = build_road_hierarchy_variant(net_file=source, output_dir=tmp_path / "out")
    assert report["status"] == "blocked" and report["reason"] == "native_scope_conversion_failed"
    assert "candidate" not in report and len(report["failed_outputs"]) == 1
    assert all(Path(row["path"]).read_text() == "<net" and row["status"] == (
        "invalid_native_output" if status == "pass" else "incomplete_native_output") for row in report["failed_outputs"])
    assert file_sha256(source) == digest


@pytest.mark.skipif(not shutil.which("netconvert") or not shutil.which("sumo"), reason="SUMO binaries required")
def test_native_ring1_road_selection(tmp_path):
    fixture_dir = Path(__file__).parents[1] / "benchmarks/hamburg_ring1_road_scope_20261020_v1"
    fixture = json.loads((fixture_dir / "fixture.json").read_text(encoding="utf-8"))
    source = tmp_path / "source.net.xml"
    source.write_bytes(gzip.decompress((fixture_dir / "native-import.net.xml.gz").read_bytes()))
    digest = file_sha256(source)
    assert digest == fixture["source_net_uncompressed_sha256"]
    report = build_road_hierarchy_variant(net_file=source, output_dir=tmp_path / "out",
        keep_tier=fixture["keep_tier"], vehicle_class=fixture["vehicle_class"], protected_way_ids=fixture["protected_way_ids"],
        netconvert_binary=shutil.which("netconvert"), sumo_binary=shutil.which("sumo"))
    assert report["status"] == "measured", report
    assert all(report["checks"].values())
    assert report["source_road_count"] == fixture["expected_source_road_count"]
    assert report["selected_road_count"] == fixture["expected_selected_road_count"]
    assert report["source_signal_cleanup"]["reference_counts"]["repairable_reference_count"] == 9
    assert report["native_attempts"][-1]["command"]["status"] == "pass"
    assert len(report["native_attempts"]) <= 2
    assert file_sha256(source) == digest


@pytest.mark.skipif(not shutil.which("netconvert") or not shutil.which("sumo"), reason="SUMO binaries required")
@pytest.mark.parametrize("invalid_controller", [False, True], ids=["valid", "unbound_vehicle_signal"])
def test_native_hierarchy_scope_keeps_protected_turns_and_removes_local_tail(tmp_path, invalid_controller):
    nodes, edges = tmp_path / "nodes.xml", tmp_path / "edges.xml"
    nodes.write_text('<nodes>' + ''.join(f'<node id="{name}" x="{x}" y="{y}"/>' for name, x, y in
        [("w",0,0),("a",100,0),("b",200,0),("e",300,0),("c",100,100),("d",100,200),("f",100,300)]) + '</nodes>', encoding="utf-8")
    definitions = [("1","w","a","primary"),("2","a","b","tertiary"),("3","b","e","primary"),
                   ("4","a","c","residential"),("5","c","d","service"),("6","d","f","service")]
    edges.write_text('<edges>' + ''.join(f'<edge id="{name}" from="{a}" to="{b}" type="highway.{kind}" allow="passenger"/>'
        for name,a,b,kind in definitions) + '</edges>', encoding="utf-8")
    types = tmp_path / "types.xml"
    types.write_text('<types>' + ''.join(f'<type id="highway.{name}" priority="{i}" numLanes="1" speed="13.89"/>'
        for i,name in enumerate(["service","residential","tertiary","primary"])) + '</types>', encoding="utf-8")
    source = tmp_path / "source.net.xml"
    subprocess.run(["netconvert","-n",str(nodes),"-e",str(edges),"-t",str(types),"-o",str(source)],
                   check=True,capture_output=True,timeout=30)
    if invalid_controller:
        tree = ET.parse(source)
        tree.getroot().find("connection").attrib.update(tl="unbound", linkIndex="0")
        tree.write(source, encoding="utf-8", xml_declaration=True)
    digest = file_sha256(source)
    call = subprocess.run([sys.executable, "-m", "astra_sumo.cli", "network", "select-roads", str(source), str(tmp_path / "out"),
        "--protect-way-id", "2", "--netconvert-binary", shutil.which("netconvert"), "--sumo-binary", shutil.which("sumo"), "--json"],
        capture_output=True,text=True,timeout=90)
    report = json.loads(call.stdout)
    if invalid_controller:
        assert call.returncode == 3 and report["status"] == "blocked", report
        assert report["reason"] == "source_has_unresolved_signal_references"
        assert report["native_conversion_count"] == 0 and "candidate" not in report
        assert file_sha256(source) == digest
        return
    assert call.returncode == 0, call.stderr
    assert report["status"] == "measured", report
    assert all(report["checks"].values())
    assert report["source_road_count"] == 6 and report["selected_road_count"] == 4
    plan = json.loads(Path(report["selection_file"]).read_text())
    assert plan["excluded_edge_ids"] == ["5","6"]
    routes = tmp_path / "probe.rou.xml"
    routes.write_text('<routes><vehicle id="through" depart="0"><route edges="1 2 3"/></vehicle>'
        '<vehicle id="side" depart="10"><route edges="1 4"/></vehicle></routes>', encoding="utf-8")
    trips = tmp_path / "trips.xml"
    driven = subprocess.run(["sumo","-n",report["candidate"]["path"],"-r",str(routes),"--end","200",
        "--tripinfo-output",str(trips),"--no-step-log","true"], capture_output=True,text=True,timeout=30)
    assert driven.returncode == 0, driven.stderr
    assert {row.get("id") for row in ET.parse(trips).getroot()} == {"through","side"}
    assert file_sha256(source) == digest
