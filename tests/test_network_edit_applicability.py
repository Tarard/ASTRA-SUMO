from __future__ import annotations

import json
from pathlib import Path
import shutil
import subprocess
from xml.etree import ElementTree as ET

import pytest
from shapely.geometry import Polygon

from astra_sumo.core.candidate_contracts import file_sha256
from astra_sumo.core.corridor_edit_ledger import (
    build_corridor_edit_ledger, materialize_corridor_edit_variant, run_corridor_candidate_gates,
)
from astra_sumo.core.network_edit_applicability import (
    AUTOMATIC_EDIT_PROFILES, _boundary_access, assess_edit_applicability, verify_network_edit,
)


def _source(tmp_path):
    nodes, edges = ET.Element("nodes"), ET.Element("edges")
    points = {"A": (0, 0), "B": (20, 0), "W": (-150, 0), "N": (0, 150), "S": (0, -150),
              "NW": (-150, 150), "E": (150, 0), "R": (500, 0), "RW": (350, 0), "RE": (650, 0),
              "P": (400, 200), "Q": (500, 200)}
    for name, (x, y) in points.items():
        ET.SubElement(nodes, "node", id=name, x=str(x), y=str(y),
                      type="traffic_light" if name == "R" else "priority")
    for left, right in [("A", "B"), ("W", "A"), ("N", "A"), ("S", "A"), ("NW", "A"),
                        ("E", "B"), ("RW", "R"), ("R", "RE")]:
        for a, b in [(left, right), (right, left)]:
            ET.SubElement(edges, "edge", id=f"{a}_{b}", **{"from": a, "to": b, "allow": "passenger bus"})
    ET.SubElement(edges, "edge", id="foot_cycle", **{"from": "P", "to": "Q", "allow": "pedestrian bicycle"})
    for name, root in [("nodes", nodes), ("edges", edges)]:
        ET.ElementTree(root).write(tmp_path / f"{name}.xml", encoding="utf-8")
    source = tmp_path / "source.net.xml"
    command = [shutil.which("netconvert"), "-n", str(tmp_path / "nodes.xml"), "-e", str(tmp_path / "edges.xml"),
               "--offset.disable-normalization", "true", "-o", str(source)]
    result = subprocess.run(command, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    return source


def _ledger(tmp_path, kind):
    source = _source(tmp_path)
    params = {"suppress_turnaround": {"from": "W_A", "fromLane": 0, "to": "A_W", "toLane": 0},
              "absorb_reciprocal_fragment": {"node_ids": ["A", "B"], "join_id": "joined", "maximum_length_m": 30}}
    if kind == "set_junction_shape":
        row = ET.parse(source).getroot().find("junction[@id='A']")
        polygon = Polygon([tuple(map(float, token.split(","))) for token in row.get("shape").split()])
        target = polygon.buffer(-0.1, join_style="mitre")
        params[kind] = {"node_id": "A", "shape": [list(point) for point in target.exterior.coords[:-1]]}
    operation = {"id": "local-edit", "operation": kind, "status": "accepted", "params": params[kind],
                 "rationale": "Synthetic accepted edit for technical verification. No field claim.",
                 "evidence": [{"path": str(source), "sha256": file_sha256(source)}],
                 "rollback": {"action": "discard_candidate_and_use_immutable_source"}}
    report = build_corridor_edit_ledger(net_file=source, output_dir=tmp_path / "ledger", operations=[operation],
                                       include_auto_proposals=False)
    assert report["status"] == "pass"
    path = Path(report["ledger_file"])
    return path, json.loads(path.read_text(encoding="utf-8"))


native = pytest.mark.skipif(not shutil.which("netconvert") or not shutil.which("sumo"), reason="SUMO required")


@native
@pytest.mark.parametrize("kind", AUTOMATIC_EDIT_PROFILES)
def test_native_profiles_work_outside_the_four_way_vehicle_only_domain(tmp_path, kind):
    path, ledger = _ledger(tmp_path, kind)
    source_root = ET.parse(ledger["source_net_file"]).getroot()
    assert len([edge for edge in source_root.findall("edge") if edge.get("to") == "A"]) == 5
    assert source_root.find("tlLogic[@id='R']") is not None
    assert "pedestrian" in source_root.find("edge[@id='foot_cycle']/lane").get("allow")
    before = file_sha256(Path(ledger["source_net_file"]))
    assert assess_edit_applicability(ledger)["status"] == "pass"
    report = verify_network_edit(ledger_file=path, output_dir=tmp_path / "verification",
                                 netconvert_binary=shutil.which("netconvert"), mode="release")
    assert report["status"] == ("review_required" if kind == "absorb_reciprocal_fragment" else "pass"), report
    assert all(value == "pass" for value in report["applicability"]["checks"].values())
    assert report["candidate_gates"]["gates"]["modal_connectivity"]["basis"] == "edit_specific_all_class_boundary_paths"
    routing = report["candidate_gates"]["routeability_report"]
    if kind == "absorb_reciprocal_fragment":
        assert routing["reason"] == "unresolved_endpoint_mapping"
        source_requests = ET.parse(routing["source_trip_file"]).getroot().findall("trip")
        assert len(source_requests) == 20
        assert {row.get("from") for row in source_requests} & {"A_B", "B_A"}
        assert any("A_B" in row["reason"] or "B_A" in row["reason"]
                   for row in routing["od_policy"]["endpoint_mapping"]["unresolved_requests"])
    else:
        assert routing["final_attempt"]["inspection"]["summary"]["arrived"] == 20
    assert file_sha256(Path(ledger["source_net_file"])) == before
    assert report["field_correctness"] == report["certification_claim"] == "not_established"
    assert report["inputs_unchanged"]


@native
def test_hash_unaccepted_and_unsupported_operations_do_not_enter_automatic_work(tmp_path):
    path, ledger = _ledger(tmp_path, "suppress_turnaround")
    original = ledger["source_sha256"]
    ledger["source_sha256"] = "0" * 64
    assert assess_edit_applicability(ledger)["reason"] == "source_hash_mismatch"
    ledger["source_sha256"] = original
    ledger["operations"][0]["status"] = "candidate"
    assert assess_edit_applicability(ledger)["status"] == "review_required"
    path.write_text(json.dumps(ledger), encoding="utf-8")
    report = verify_network_edit(ledger_file=path, output_dir=tmp_path / "verification", mode="release")
    assert report["status"] == "review_required"
    assert not (tmp_path / "verification/candidate").exists()
    ledger["operations"][0]["operation"] = "set_lane_width"
    assert assess_edit_applicability(ledger)["status"] == "not_applicable"


@native
def test_expansion_and_local_grade_structure_require_review(tmp_path):
    _, ledger = _ledger(tmp_path, "set_junction_shape")
    operation = ledger["operations"][0]
    operation["params"]["shape"] = [[-50, -50], [50, -50], [50, 50], [-50, 50]]
    assert assess_edit_applicability(ledger)["reason"] == "shape_is_not_a_strict_tightening"
    source = Path(ledger["source_net_file"])
    tree = ET.parse(source)
    ET.SubElement(tree.getroot().find("edge[@id='W_A']"), "param", key="bridge", value="yes")
    tree.write(source, encoding="utf-8")
    ledger["source_sha256"] = file_sha256(source)
    operation["evidence"][0]["sha256"] = ledger["source_sha256"]
    assert assess_edit_applicability(ledger)["reason"] == "local_grade_or_rail_semantics"


def test_all_vehicle_classes_are_checked_without_an_exterior_detour():
    root = ET.fromstring('''<net>
      <edge id="in" from="west" to="a"><lane id="in_0" allow="passenger bicycle pedestrian"/></edge>
      <edge id="mid" from="a" to="b"><lane id="mid_0" allow="passenger bicycle pedestrian"/></edge>
      <edge id="out" from="b" to="east"><lane id="out_0" allow="passenger bicycle pedestrian"/></edge>
      <connection from="in" fromLane="0" to="mid" toLane="0"/>
      <connection from="mid" fromLane="0" to="out" toLane="0"/>
    </net>''')
    assert _boundary_access(root, {"a", "b"})[2] == {("in_0", "out_0"): {"passenger", "bicycle", "pedestrian"}}
    root.find("connection[@from='mid']").set("disallow", "bicycle")
    assert _boundary_access(root, {"a", "b"})[2] == {("in_0", "out_0"): {"passenger", "bicycle", "pedestrian"}}
    root.find("edge[@id='mid']/lane").set("allow", "passenger pedestrian")
    assert _boundary_access(root, {"a", "b"})[2] == {("in_0", "out_0"): {"passenger", "pedestrian"}}
    root.find("edge[@id='mid']").set("to", "external")
    assert ("in_0", "out_0") not in _boundary_access(root, {"a", "b"})[2]


def test_shape_that_collapses_at_output_precision_is_rejected():
    from astra_sumo.core.network_edit_operations import validate_scoped_operation

    operation = {"operation": "set_junction_shape", "params": {
        "node_id": "j", "shape": [[0, 0], [1e-14, 0], [0, 1e-14]]}}
    assert validate_scoped_operation(operation)


@native
@pytest.mark.parametrize("damage", ["bus_access", "remote_geometry"])
def test_changed_boundary_mode_or_remote_geometry_blocks_applicability(tmp_path, damage):
    _, ledger = _ledger(tmp_path, "set_junction_shape")
    candidate = tmp_path / "candidate.net.xml"
    tree = ET.parse(ledger["source_net_file"])
    target = ledger["operations"][0]["params"]["shape"]
    tree.getroot().find("junction[@id='A']").set("shape", " ".join(f"{x},{y}" for x, y in target))
    if damage == "bus_access":
        tree.getroot().find("edge[@id='W_A']/lane").set("allow", "passenger")
    else:
        tree.getroot().find("edge[@id='foot_cycle']/lane").set("length", "999")
    tree.write(candidate, encoding="utf-8")
    result = assess_edit_applicability(ledger, candidate_net_file=candidate)
    assert result["status"] == "blocked"
    if damage == "bus_access":
        assert result["checks"]["all_class_explicit_boundary_paths"] == "blocked"
        assert any("bus" in row["expected"] for row in result["boundary_paths"]["differences"])
    else:
        assert result["checks"]["outside_state"] == "blocked"


@native
def test_stale_ledger_cannot_replace_the_existing_modal_gate(tmp_path):
    path, ledger = _ledger(tmp_path, "suppress_turnaround")
    built = materialize_corridor_edit_variant(ledger_file=path, output_dir=tmp_path / "candidate",
                                              netconvert_binary=shutil.which("netconvert"))
    assert built["status"] == "pass"
    ledger["operations"][0]["rationale"] += " Changed after materialization."
    path.write_text(json.dumps(ledger), encoding="utf-8")
    report = run_corridor_candidate_gates(source_net_file=Path(ledger["source_net_file"]),
        candidate_net_file=Path(built["candidate_net_file"]), materialization_report=built,
        edit_ledger_file=path, output_dir=tmp_path / "gates")
    assert report["status"] == "blocked"
    assert "materialized ledger hash" in report["gates"]["edit_applicability"]["error"]
    assert report["gates"]["modal_connectivity"].get("basis") != "edit_specific_all_class_boundary_paths"
    assert report["gates"]["sumo_load"]["execution_status"] == "not_run"


@native
def test_turn_removal_that_strands_a_class_requires_review(tmp_path):
    _, ledger = _ledger(tmp_path, "suppress_turnaround")
    source = Path(ledger["source_net_file"])
    tree = ET.parse(source)
    for row in list(tree.getroot().findall("connection")):
        if row.get("from") == "W_A" and row.get("to") != "A_W":
            tree.getroot().remove(row)
    tree.write(source, encoding="utf-8")
    ledger["source_sha256"] = file_sha256(source)
    ledger["operations"][0]["evidence"][0]["sha256"] = ledger["source_sha256"]
    assert assess_edit_applicability(ledger)["reason"] == "turn_removal_strands_a_vehicle_class"


@native
def test_local_pedestrian_absorption_requires_route_evidence(tmp_path):
    _, ledger = _ledger(tmp_path, "absorb_reciprocal_fragment")
    source = Path(ledger["source_net_file"])
    tree = ET.parse(source)
    tree.getroot().find("edge[@id='A_B']/lane").set("allow", "passenger bus pedestrian")
    tree.write(source, encoding="utf-8")
    ledger["source_sha256"] = file_sha256(source)
    ledger["operations"][0]["evidence"][0]["sha256"] = ledger["source_sha256"]
    assert assess_edit_applicability(ledger)["reason"] == "local_pedestrian_join_requires_walking_route_evidence"


@native
def test_a_new_independent_hard_finding_blocks_local_approval(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from astra_sumo.corridor import conflict_graph

    _, ledger = _ledger(tmp_path, "set_junction_shape")
    candidate = tmp_path / "candidate.net.xml"
    tree = ET.parse(ledger["source_net_file"])
    target = ledger["operations"][0]["params"]["shape"]
    tree.getroot().find("junction[@id='A']").set("shape", " ".join(f"{x},{y}" for x, y in target))
    tree.write(candidate, encoding="utf-8")
    digest = file_sha256(candidate)
    hard = SimpleNamespace(witness_signature="injected-new-conflict", severity=SimpleNamespace(value="safety"))
    monkeypatch.setattr(conflict_graph, "audit_independent_movement_safety", lambda snapshot: SimpleNamespace(
        findings=[hard] if snapshot.source_sha256 == digest else []))
    result = assess_edit_applicability(ledger, candidate_net_file=candidate)
    assert result["status"] == "blocked"
    assert result["checks"]["no_new_independent_safety_findings"] == "blocked"


def test_cli_dispatches_edit_verification_without_adding_a_public_mcp_tool(monkeypatch, capsys):
    from astra_sumo.cli import main
    from astra_sumo.core import network_edit_applicability

    received = {}

    def verify(**kwargs):
        received.update(kwargs)
        return {"status": "not_applicable"}

    monkeypatch.setattr(network_edit_applicability, "verify_network_edit", verify)
    assert main(["network", "verify-edit", "ledger.json", "new-output", "--json"]) == 1
    assert json.loads(capsys.readouterr().out)["status"] == "not_applicable"
    assert received["ledger_file"] == "ledger.json"
