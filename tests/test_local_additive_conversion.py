from copy import deepcopy
import gzip
from pathlib import Path
import shutil
import xml.etree.ElementTree as ET

import pytest

from astra_sumo.core.candidate_contracts import file_sha256, validate_materialization_evidence
from astra_sumo.core.command_runner import run_command
from astra_sumo.core.corridor_edit_ledger import materialize_corridor_edit_variant, _bound_scoped_edit_plan
from astra_sumo.core.local_network_conversion import (
    legacy_additive_edit_scope,
    validate_local_conversion,
    loaded_turn_permissions,
    local_conversion_input,
)
from astra_sumo.core.sumo_commands import run_sumo_load_audit


def test_plain_additions_cannot_modify_an_existing_road(tmp_path):
    root = ET.fromstring('<net><junction id="a"/><junction id="b"/><edge id="old" from="a" to="b"/></net>')
    patch = tmp_path / "edges.xml"
    patch.write_text('<edges><edge id="old" from="a" to="b"/></edges>', encoding="utf-8")
    with pytest.raises(ValueError, match="new identity"):
        legacy_additive_edit_scope(root, ["netconvert", "--edge-files", str(patch)])


def test_crossing_scope_requires_incident_roads(tmp_path):
    root = ET.fromstring(
        '<net><junction id="a"/><junction id="b"/><junction id="c"/><edge id="old" from="a" to="b"/></net>'
    )
    patch = tmp_path / "connections.xml"
    patch.write_text('<connections><crossing node="c" edges="old"/></connections>', encoding="utf-8")
    with pytest.raises(ValueError, match="meet the declared junction"):
        legacy_additive_edit_scope(root, ["netconvert", "--connection-files", str(patch)])


def test_wider_native_context_does_not_expand_shared_controller_adoption_scope():
    root = ET.fromstring("""<net version="1.20"><location netOffset="0,0" projParameter="!"/>
      <edge id="in" from="w" to="a"><lane id="in_0" index="0" allow="passenger"/></edge>
      <edge id="middle" from="a" to="b"><lane id="middle_0" index="0" allow="passenger"/></edge>
      <edge id="out" from="b" to="e"><lane id="out_0" index="0" allow="passenger"/></edge>
      <junction id="w"/><junction id="a"/><junction id="b"/><junction id="e"/>
      <tlLogic id="shared" type="static" programID="0"><phase duration="30" state="GG"/></tlLogic>
      <connection from="in" to="middle" fromLane="0" toLane="0" tl="shared" linkIndex="0"/>
      <connection from="middle" to="out" fromLane="0" toLane="0" tl="shared" linkIndex="1"/></net>""")
    subset, record = local_conversion_input(root, {"a"}, {"in", "middle"}, rebuild_adjacent_junctions=True)
    assert subset is None and record["status"] == "full_network"
    assert record["reason"] == "controller_spans_edit_boundary" and record["controller_ids"] == ["shared"]


def _source(folder):
    nodes, edges = folder / "nodes.xml", folder / "edges.xml"
    nodes.write_text(
        '<nodes><node id="a" x="0" y="0"/><node id="b" x="50" y="0"/>'
        '<node id="c" x="100" y="0"/><node id="R" x="1000" y="0"/><node id="S" x="1100" y="0"/></nodes>',
        encoding="utf-8",
    )
    records = [("f0", "a", "b"), ("f1", "b", "c"), ("r0", "c", "b"), ("r1", "b", "a"), ("far", "R", "S")]
    root = ET.Element("edges")
    for identity, first, last in records:
        ET.SubElement(
            root,
            "edge",
            id=identity,
            attrib={"from": first, "to": last, "allow": "passenger bus bicycle", "sidewalkWidth": "2"},
        )
    ET.ElementTree(root).write(edges, encoding="utf-8")
    source = folder / "source.net.xml"
    result = run_command(
        [
            shutil.which("netconvert"),
            "-n",
            str(nodes),
            "-e",
            str(edges),
            "--offset.disable-normalization",
            "true",
            "-o",
            str(source),
        ],
        cwd=folder,
        timeout_seconds=30,
    )
    assert result.returncode == 0, result
    tree = ET.parse(source)
    for index, (identity, modes) in enumerate(
        (
            ("highway.secondary", "passenger bus bicycle"),
            ("highway.footway", "pedestrian"),
            ("highway.primary_link", "passenger bus"),
        )
    ):
        tree.getroot().insert(index + 1, ET.Element("type", id=identity, allow=modes, numLanes="1", speed="10"))
    connection = tree.getroot().find("connection[@from='f0'][@to='f1'][@fromLane='1'][@toLane='1']")
    assert connection is not None
    tree.getroot().find(f"edge/lane[@id='{connection.get('via')}']").set("allow", "bus")
    tree.write(source, encoding="utf-8", xml_declaration=True)
    return source


@pytest.mark.skipif(not shutil.which("netconvert") or not shutil.which("sumo"), reason="SUMO programs required")
@pytest.mark.parametrize("kind", ["add_edge", "add_sidewalk", "add_ramp", "add_crossing"])
def test_native_additions_use_local_context_and_replay_bound_plain_inputs(tmp_path, kind):
    source = _source(tmp_path)
    original = source.read_bytes()
    params = (
        {"node_id": "b", "crossing_edges": ["f0", "r1"]}
        if kind == "add_crossing"
        else {
            "id": "new-road",
            "from": "a",
            "to": "b" if kind == "add_sidewalk" else "c",
            "type": "highway.footway"
            if kind == "add_sidewalk"
            else "highway.primary_link"
            if kind == "add_ramp"
            else "highway.secondary",
        }
    )
    operation = {
        "id": kind,
        "operation": kind,
        "status": "accepted",
        "target_ids": [],
        "params": params,
        "rationale": "Native additive preservation control.",
        "evidence": [{"path": str(source), "sha256": file_sha256(source)}],
        "rollback": {"action": "discard_candidate"},
    }
    report = materialize_corridor_edit_variant(
        output_dir=tmp_path / "edit",
        ledger={"source_net_file": str(source), "source_sha256": file_sha256(source), "operations": [operation]},
        netconvert_binary=shutil.which("netconvert"),
    )
    assert report["status"] == "pass", report
    local = report["semantic_validation"]["local_conversion"]
    assert local["status"] == "local" and local["converted_normal_edge_count"] == 4
    assert local["source_normal_edge_count"] == 5
    assert validate_local_conversion(local, source, command=report["command"]) == Path(local["input"]["path"])
    candidate = Path(report["candidate_net_file"])
    evidence = validate_materialization_evidence(report, source_net_file=source, candidate_net_file=candidate)
    assert evidence["status"] == "pass", evidence
    scope = _bound_scoped_edit_plan(report, evidence, source, ET.parse(source).getroot())
    assert scope["scope_binding"]["basis"] == "manifest_bound_native_additive_inputs"
    assert not {"R", "S"}.intersection(scope["mutable_junction_ids"])
    forged = deepcopy(report)
    forged.update(operation_family="delete_edge", target_edge_ids=["far"])
    assert _bound_scoped_edit_plan(forged, evidence, source, ET.parse(source).getroot()) == scope
    before, after = ET.parse(source).getroot(), ET.parse(candidate).getroot()
    assert loaded_turn_permissions(before)[("f0", 1, "f1", 1)] == {"bus"}
    assert loaded_turn_permissions(after)[("f0", 1, "f1", 1)] == {"bus"}
    states = [
        [(row.tag, row.attrib, (row.text or "").strip()) for row in root.find("edge[@id='far']").iter()]
        for root in (before, after)
    ]
    assert states[0] == states[1]
    loaded = run_sumo_load_audit(net_file=candidate, output_dir=tmp_path / "load", sumo_binary=shutil.which("sumo"))
    assert loaded["status"] == "pass", loaded
    assert source.read_bytes() == original


@pytest.mark.parametrize("actual_lanes", [2, 3])
def test_added_lane_count_separates_input_lanes_from_type_sidewalk(tmp_path, actual_lanes):
    from astra_sumo.core.corridor_edit_ledger import _modal_addition_gate

    source = ET.fromstring("""<net version="1.20"><type id="residential" sidewalkWidth="2"/>
      <junction id="a"/><junction id="b"/>
      <edge id="old0" from="a" to="b"><lane id="old0_0" index="0" allow="pedestrian"/></edge>
      <edge id="old1" from="b" to="a"><lane id="old1_0" index="0" allow="pedestrian"/></edge>
      <connection from="old0" to="old1" fromLane="0" toLane="0"/>
      <connection from="old1" to="old0" fromLane="0" toLane="0"/></net>""")
    candidate = deepcopy(source)
    ET.SubElement(candidate, "edge", id="new", attrib={"from": "a", "to": "b"})
    ET.SubElement(candidate, "connection", attrib={"from": "new", "to": "old1", "fromLane": "0", "toLane": "0"})
    ET.SubElement(candidate, "connection", attrib={"from": "old1", "to": "new", "fromLane": "0", "toLane": "0"})
    for index in range(actual_lanes):
        ET.SubElement(
            candidate.find("edge[@id='new']"),
            "lane",
            id=f"new_{index}",
            index=str(index),
            allow="pedestrian" if index == 0 else "passenger",
        )
    result = _modal_addition_gate(
        source,
        candidate,
        [
            {
                "edge_id": "new",
                "operation": "add_edge",
                "type": "residential",
                "allow": "passenger",
                "requested_native_num_lanes": 1,
                "native_permissions": {"allow": "passenger"},
            }
        ],
    )
    assert result["status"] == ("pass" if actual_lanes == 2 else "blocked")
    layout = result["added_lane_layouts"][0]
    assert layout["input_num_lanes"] == 1 and layout["type_added_sidewalk_lanes"] == 1
    assert layout["expected_total_lanes"] == 2 and layout["built_total_lanes"] == actual_lanes


@pytest.mark.skipif(not shutil.which("netconvert"), reason="SUMO programs required")
def test_native_type_adds_declared_sidewalk_and_bicycle_lanes(tmp_path):
    source = _source(tmp_path)
    tree = ET.parse(source)
    tree.getroot().insert(
        1, ET.Element("type", id="typed-extras", sidewalkWidth="2", bikeLaneWidth="1", numLanes="1", speed="10")
    )
    tree.write(source, encoding="utf-8", xml_declaration=True)
    operation = {
        "id": "typed-lanes",
        "operation": "add_edge",
        "status": "accepted",
        "target_ids": [],
        "params": {"id": "typed-road", "from": "a", "to": "c", "type": "typed-extras", "lanes": 1},
        "rationale": "Native type-derived lane count control.",
        "evidence": [{"path": str(source), "sha256": file_sha256(source)}],
        "rollback": {"action": "discard_candidate"},
    }
    report = materialize_corridor_edit_variant(
        output_dir=tmp_path / "edit",
        ledger={"source_net_file": str(source), "source_sha256": file_sha256(source), "operations": [operation]},
        netconvert_binary=shutil.which("netconvert"),
    )
    assert report["command_result"]["returncode"] == 0, report
    assert report["status"] == "blocked"
    modal = report["semantic_validation"]["modal_connectivity"]
    assert any(error["code"] == "modal_graph_decreased" for error in modal["errors"])
    assert not any(error["code"] == "added_lane_count_not_materialized" for error in modal["errors"])
    layout = modal["added_lane_layouts"][0]
    assert layout["input_num_lanes"] == 1 and layout["type_added_sidewalk_lanes"] == 1
    assert (
        layout["type_added_bicycle_lanes"] == 1 and layout["built_total_lanes"] == layout["expected_total_lanes"] == 3
    )


@pytest.mark.skipif(not shutil.which("netconvert") or not shutil.which("sumo"), reason="SUMO programs required")
@pytest.mark.parametrize("full_input", [False, True])
def test_hamburg_added_sidewalk_reaches_its_walking_area(tmp_path, monkeypatch, full_input):
    fixture = Path(__file__).parents[1] / "benchmarks/hamburg_loaded_links_20261018_v1/candidate.net.xml.gz"
    source = tmp_path / "hamburg.net.xml"
    source.write_bytes(gzip.decompress(fixture.read_bytes()))
    original = source.read_bytes()
    if full_input:
        monkeypatch.setattr(
            "astra_sumo.core.local_network_conversion.local_conversion_input",
            lambda *args, **kwargs: (None, {"status": "full_network", "reason": "controlled_full_comparison"}),
        )
    operation = {
        "id": "walking-area-context",
        "operation": "add_edge",
        "status": "accepted",
        "target_ids": [],
        "params": {
            "id": "new-residential",
            "from": "12865383036",
            "to": "5353176677",
            "type": "highway.residential",
            "lanes": 1,
        },
        "rationale": "Real native context regression, not field acceptance.",
        "evidence": [{"path": str(source), "sha256": file_sha256(source)}],
        "rollback": {"action": "discard_candidate"},
    }
    report = materialize_corridor_edit_variant(
        output_dir=tmp_path / "edit",
        ledger={"source_net_file": str(source), "source_sha256": file_sha256(source), "operations": [operation]},
        netconvert_binary=shutil.which("netconvert"),
    )
    assert report["status"] == "pass", report
    root = ET.parse(report["candidate_net_file"]).getroot()
    outgoing = root.findall("connection[@from='new-residential'][@fromLane='0']")
    assert any(row.get("to", "").startswith(":5353176677_w") for row in outgoing)
    if not full_input:
        local = report["semantic_validation"]["local_conversion"]
        assert local["converted_normal_edge_count"] == 14
        assert local["converted_normal_edge_count"] < local["source_normal_edge_count"]
        assert set(local["mutable_junction_ids"]) < set(local["native_rebuild_junction_ids"])
        assert local["controller_ids"] == []
        assert validate_local_conversion(local, source, command=report["command"]) == Path(local["input"]["path"])
    assert source.read_bytes() == original
