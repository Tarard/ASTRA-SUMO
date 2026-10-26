"""Declared target deltas of fragility-fix Build goals (twin/declared_targets.py, goal.create_build_goal(declared=)):
derivation, application, lint, the model-justified merge slot and the frozen goal block. No test starts SUMO; the
store-backed check reads the real F2 gate profile (skipped when the fragility store is absent)."""

from __future__ import annotations

import copy
import json
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from astra_sumo.core.reference_net_facts import build_reference_net_facts
from astra_sumo.corridor.enums import TrafficSide
from astra_sumo.corridor.synthetic_networks import build_synthetic_fixture
from astra_sumo.twin import build_profile, declared_targets as dt, goal

ROOT = Path(__file__).resolve().parents[1]
STORE = Path(r"E:\twin-store\fragility")
LOCATION = '<location netOffset="0.00,0.00" convBoundary="-100,-100,100,100" origBoundary="-100,-100,100,100" ' \
           'projParameter="!"/>'
SCOPE = {"mutable_junction_ids": ["J0"],
         "mutable_edge_ids": ["E_in", "E_out", "N_in", "N_out", "S_in", "S_out", "W_in", "W_out"]}
MERGE = {"W_in|N_out|2": {"basis": dt.MERGE_BASIS, "source_lanes": ["1", "2"], "site": "S01",
                          "label": "model-justified (simulation fragility S01), no field evidence"}}
DELETE = {"operation": "delete_lane_connection", "params": {"from": "W_in", "fromLane": 1, "to": "E_out", "toLane": 1}}
ADD = {"operation": "add_lane_connection", "params": {"from": "W_in", "fromLane": 1, "to": "N_out", "toLane": 2}}


def _derived(*ops: dict) -> dict:
    return {"method": "test", "steps": [{"candidate": f"T{index}", "op": op} for index, op in enumerate(ops)]}


def _x4() -> ET.Element:
    root = build_synthetic_fixture("standard-x4", traffic_side=TrafficSide.RIGHT)
    root.insert(0, ET.fromstring(LOCATION))
    return root


def _without_link(root: ET.Element, from_lane: str, to_lane: str) -> ET.Element:
    """``root`` without one controlled connection, later link indices shifted down and the programs rewritten, as
    netconvert renumbers them."""
    result = copy.deepcopy(root)
    gone = next(row for row in result.findall("connection")
                if f"{row.get('from')}_{row.get('fromLane')}" == from_lane
                and f"{row.get('to')}_{row.get('toLane')}" == to_lane)
    index = int(gone.get("linkIndex"))
    result.remove(gone)
    for row in result.findall("connection"):
        if row.get("tl") and int(row.get("linkIndex")) > index:
            row.set("linkIndex", str(int(row.get("linkIndex")) - 1))
    for phase in result.iter("phase"):
        state = phase.get("state")
        phase.set("state", state[:index] + state[index + 1:])
    return result


def _tables(tmp_path: Path, source: ET.Element, candidate: ET.Element) -> tuple[Path, dict, dict]:
    source_file = tmp_path / "source.net.xml"
    source_file.write_bytes(ET.tostring(source, encoding="utf-8", xml_declaration=True))
    candidate_file = tmp_path / "candidate.net.xml"
    candidate_file.write_bytes(ET.tostring(candidate, encoding="utf-8", xml_declaration=True))
    tables = [build_reference_net_facts(path, site_id="x4", target_date="2026-10-26")[0]
              for path in (source_file, candidate_file)]
    return source_file, tables[0], tables[1]


def _edge_to(root: ET.Element) -> dict[str, str]:
    return {edge.get("id"): edge.get("to") for edge in root.findall("edge") if edge.get("function") is None}


def _block(entries: list[dict], **extra) -> dict:
    return {"tag": dt.TAG, "mode": "explore", "entries": entries, "derived_from": _derived(DELETE),
            "control": {"removed_links": [{"pair": ["W_in_1", "E_out_1"], "tls_id": "J0"}], "added_links": []},
            **extra}


def _added_entries(before: dict) -> list[dict]:
    """The hand-written delta of ADD on the x4 source (no renumbering: the new link takes the next free index)."""
    facts = {fact["id"]: fact for fact in before["facts"]}
    movement = facts["lanes:W_in_1:movement_inventory"]["expected"]
    return [{"fact": "lanes:W_in_1:movement_inventory", "kind": "movement_inventory", "action": "set",
             "expected": sorted([*movement, ["W_in_1", "N_out_2"]]), "parent_expected": movement,
             "anchor": "junction:J0", "reason": "movement_changed", "tag": dt.TAG},
            {"fact": "pair:W_in_1>N_out_2:signal_group", "kind": "signal_group", "action": "add",
             "selector": {"lane_pair": ["W_in_1", "N_out_2"]}, "expected": {"tls_id": "J0", "link_index": 12},
             "anchor": "junction:J0", "reason": "link_added", "tag": dt.TAG}]


def _added_block(before: dict, **extra) -> dict:
    return {"tag": dt.TAG, "mode": "explore", "entries": _added_entries(before), "derived_from": _derived(ADD),
            "control": {"removed_links": [], "added_links": [
                {"pair": ["W_in_1", "N_out_2"], "tls_id": "J0", "sibling_pair": ["W_in_2", "N_out_2"],
                 "copy_rule": "minor_where_yielding"}]}, **extra}


def test_derive_names_only_the_changed_movement_and_signal_facts(tmp_path):
    source = _x4()
    _, before, after = _tables(tmp_path, source, _without_link(source, "W_in_1", "E_out_1"))
    entries = dt.derive_targets(before, after, edge_to=_edge_to(source), junctions={"J0"})
    by_reason = {}
    for entry in entries:
        by_reason.setdefault(entry["reason"], []).append(entry["fact"])
    assert by_reason["movement_changed"] == ["lanes:W_in_1:movement_inventory"]
    assert by_reason["link_removed"] == ["pair:W_in_1>E_out_1:signal_group"]
    assert len(by_reason["link_renumbered"]) == 10  # links 2..11 move to 1..10
    assert {entry["anchor"] for entry in entries} == {"junction:J0"} and all(e["tag"] == dt.TAG for e in entries)
    assert dt.derive_targets(before, after, edge_to=_edge_to(source), junctions={"W"}) == []
    applied = dt.apply_targets(before, entries)
    assert [fact["id"] for fact in applied["facts"]] == [fact["id"] for fact in after["facts"]]
    assert [fact["expected"] for fact in applied["facts"]] == [fact["expected"] for fact in after["facts"]]
    marked = [fact for fact in applied["facts"] if "declared_target" in fact["mapping_diagnostics"]]
    assert len(marked) == 11 and applied is not before and len(before["facts"]) == len(after["facts"]) + 1


def test_apply_refuses_a_stale_parent_value_or_an_existing_add(tmp_path):
    source = _x4()
    _, before, after = _tables(tmp_path, source, _without_link(source, "W_in_1", "E_out_1"))
    entries = dt.derive_targets(before, after, edge_to=_edge_to(source), junctions={"J0"})
    stale = copy.deepcopy(entries)
    next(e for e in stale if e["action"] == "set")["parent_expected"] = "other"
    with pytest.raises(ValueError, match="parent value"):
        dt.apply_targets(before, stale)
    added = {"fact": "lanes:W_in_0:movement_inventory", "kind": "movement_inventory", "action": "add",
             "selector": {"incoming_lane_ids": ["W_in_0"]}, "expected": [], "anchor": "junction:J0",
             "reason": "movement_changed", "tag": dt.TAG}
    with pytest.raises(ValueError, match="existing fact"):
        dt.apply_targets(before, [added])
    with pytest.raises(ValueError, match="different targets"):
        dt.merge_entries([entries[0]], [{**entries[0], "expected": [["W_in_1", "S_out_0"]]}])


def test_lint_refuses_out_of_scope_wrong_kind_untagged_and_undeclared_control(tmp_path):
    source = _x4()
    _, before, after = _tables(tmp_path, source, _without_link(source, "W_in_1", "E_out_1"))
    entries = dt.derive_targets(before, after, edge_to=_edge_to(source), junctions={"J0"})
    assert dt.lint_targets(_block(entries), SCOPE) == []
    assert dt.lint_targets(_added_block(before, evidence_justified_destination_merges=MERGE), SCOPE) == []
    # A merge no declared add op creates is refused (here the only op is a delete).
    assert any("no declared add op creates this merge" in e
               for e in dt.lint_targets(_block(entries, evidence_justified_destination_merges=MERGE), SCOPE))
    narrow = {"mutable_junction_ids": ["W"], "mutable_edge_ids": ["W_out"]}
    assert any("outside the scope" in e for e in dt.lint_targets(_block(entries), narrow))
    assert any("not on a mutable edge" in e for e in dt.lint_targets(_block(entries), narrow))
    wrong = [{**entries[0], "kind": "lane_width"}]
    assert any("not a movement or signal kind" in e for e in dt.lint_targets(_block(wrong), SCOPE))
    untagged = [{**entries[0], "tag": "other"}]
    assert any("not tagged" in e for e in dt.lint_targets(_block(untagged), SCOPE))
    undeclared = {**_block(entries), "control": {"removed_links": [], "added_links": []}}
    assert any("removed link without a control declaration" in e for e in dt.lint_targets(undeclared, SCOPE))
    claimed = {key: {**value, "label": "official"} for key, value in MERGE.items()}
    assert any("no field evidence" in e
               for e in dt.lint_targets(_added_block(before, evidence_justified_destination_merges=claimed), SCOPE))
    assert any("explore only" in e for e in dt.lint_targets({**_block(entries), "mode": "release"}, SCOPE))


def test_lint_refuses_entries_the_declared_op_does_not_imply(tmp_path):
    """The delta is fitted to a materialization, so V = 0 holds by construction: lint must tie every entry to the
    declared typed op."""
    source = _x4()
    _, before, after = _tables(tmp_path, source, _without_link(source, "W_in_1", "E_out_1"))
    entries = dt.derive_targets(before, after, edge_to=_edge_to(source), junctions={"J0"})
    ops, _ = dt.declared_ops(_block(entries))
    assert dt.implied_errors(entries, ops, base=before) == []
    by_fact = {entry["fact"]: entry for entry in entries}
    movement = by_fact["lanes:W_in_1:movement_inventory"]
    renumbered = next(entry for entry in entries if entry["reason"] == "link_renumbered")

    def errors(changed: list[dict], **extra) -> list[str]:
        return dt.lint_targets(_block(changed, **extra), SCOPE)

    def swap(entry: dict, **values) -> list[dict]:
        return [({**row, **values} if row is entry else row) for row in entries]

    assert any("derived_from names no step" in e for e in errors(entries, derived_from={"method": "test"}))
    assert any("plus or minus the ops' pairs" in e
               for e in errors(swap(movement, expected=[*movement["expected"], ["W_in_1", "S_out_0"]])))
    other = {**movement, "fact": "lanes:W_in_0:movement_inventory"}
    assert any("no declared op starts on W_in_0" in e for e in errors([*entries, other]))
    assert any("declares no movement change" in e for e in errors([e for e in entries if e is not movement]))
    fake_add = {"fact": "pair:W_in_0>N_out_1:signal_group", "kind": "signal_group", "action": "add",
                "selector": {"lane_pair": ["W_in_0", "N_out_1"]}, "expected": {"tls_id": "J0", "link_index": 11},
                "anchor": "junction:J0", "reason": "link_added", "tag": dt.TAG}
    assert any("must be an add op's own pair" in e for e in errors([*entries, fake_add]))
    moved = swap(renumbered, expected={**renumbered["expected"], "tls_id": "J9"})
    assert any("keeps its controller" in e for e in errors(moved))
    assert any("not implied by a lane-connection op" in e for e in errors(swap(renumbered, reason="binding_changed")))
    assert any("not a delete op's pair" in e for e in errors(entries, control={
        "removed_links": [{"pair": ["W_in_1", "E_out_1"], "tls_id": "J0"}, {"pair": ["W_in_0", "S_out_0"],
                                                                             "tls_id": "J0"}], "added_links": []}))
    assert any("not a typed lane-connection op" in e
               for e in errors(entries, derived_from=_derived({"operation": "set_lane_count", "params": {}})))
    # With the source's facts: the controller's links must stay a bijection onto 0..n-1.
    duplicate = swap(renumbered, expected={**renumbered["expected"], "link_index": 0})
    assert any("not a bijection" in e for e in dt.implied_errors(duplicate, ops, base=before))
    without_removal = [entry for entry in entries if entry["reason"] != "link_removed"]
    found = dt.implied_errors(without_removal, ops, base=before)
    assert any("without a link_removed entry" in e for e in found) and any("not a bijection" in e for e in found)


def test_goal_create_refuses_a_delta_the_ops_do_not_imply(tmp_path):
    source = _x4()
    source_file, before, after = _tables(tmp_path, source, _without_link(source, "W_in_1", "E_out_1"))
    entries = dt.derive_targets(before, after, edge_to=_edge_to(source), junctions={"J0"})
    renumbered = next(entry for entry in entries if entry["reason"] == "link_renumbered")
    shifted = [({**row, "expected": {**row["expected"], "link_index": 0}} if row is renumbered else row)
               for row in entries]
    declared = {"entries": shifted, "derived_from": _derived(DELETE),
                "control": {"parent_control": {"path": "control.add.xml", "sha256": "0" * 64},
                            "removed_links": [{"pair": ["W_in_1", "E_out_1"], "tls_id": "J0"}], "rule": "test"}}
    with pytest.raises(ValueError, match="the declared ops do not imply"):
        goal.create_build_goal(ROOT, net=source_file, site_id="x4", target_date="2026-10-26",
                               mutable_junction_ids=["J0"], mutable_edge_ids=SCOPE["mutable_edge_ids"],
                               ops=["delete_lane_connection"], seeds=[42], scratch_dir=tmp_path, declared=declared)


def _reshaped(root: ET.Element) -> ET.Element:
    """``root`` with link W_in_1 -> E_out_1 removed and the internal lane of W_in_0 -> S_out_0 moved by 1 m north,
    0.5 m longer and slower, as a local re-derivation does to an untouched path."""
    result = _without_link(root, "W_in_1", "E_out_1")
    lane = next(lane for lane in result.iter("lane") if lane.get("id") == ":J0_0_0")
    lane.set("shape", " ".join(f"{float(x):.2f},{float(y) + 1:.2f}" for x, y in
                               (point.split(",") for point in lane.get("shape").split())))
    lane.set("length", f"{float(lane.get('length')) + 0.5:.2f}")
    lane.set("speed", f"{float(lane.get('speed')) - 1:.2f}")
    return result


def test_side_effects_report_untouched_paths_the_materialization_moved():
    source = _x4()
    found = dt.materialized_side_effects(source, _reshaped(source), ["J0"], {("W_in_1", "E_out_1")})
    assert found["retained"] == 11
    summary = found["summary"]
    assert list(summary["reshaped_over_0.5m"]["connections"]) == ["W_in_0>S_out_0"]
    assert summary["reshaped_over_0.5m"]["range"] == [1.0, 1.0]
    assert summary["length_change_over_0.1m"]["connections"] == {"W_in_0>S_out_0": 0.5}
    assert list(summary["speed_change_over_0.01m_s"]["connections"]) == ["W_in_0>S_out_0"]
    assert summary["right_of_way_changed_beyond_op_pairs"] == []
    unchanged = dt.materialized_side_effects(source, _without_link(source, "W_in_1", "E_out_1"), ["J0"],
                                             {("W_in_1", "E_out_1")})
    assert unchanged["summary"]["reshaped_over_tolerance"]["count"] == 0
    assert dt.hausdorff([(0.0, 0.0), (10.0, 0.0)], [(0.0, 1.0), (10.0, 1.0)]) == pytest.approx(1.0)


@pytest.mark.skipif(not (STORE / "candidates/F2/network.net.xml").is_file(),
                    reason="the fragility store E:\\twin-store\\fragility has no candidates")
def test_the_real_candidates_side_effects():
    parent = ET.parse(STORE / "inputs/6c52382e7664eca33fceff6f/network.net.xml").getroot()
    f1 = dt.materialized_side_effects(parent, ET.parse(STORE / "candidates/F1/network.net.xml").getroot(),
                                      ["LSA535_part0"], {("186821027#0_4", "186821036#1_1")})["summary"]
    assert f1["reshaped_over_0.5m"]["count"] == 5 and f1["reshaped_over_0.5m"]["range"] == [2.498, 3.884]
    assert f1["wait_point_shift_over_0.5m"]["count"] == 3 and f1["right_of_way_changed_beyond_op_pairs"] == []
    assert f1["speed_change_over_0.01m_s"]["connections"]["186821034#0_2>43305966#1_0"] == pytest.approx(0.483682)
    f2 = dt.materialized_side_effects(parent, ET.parse(STORE / "candidates/F2/network.net.xml").getroot(),
                                      ["266197536"], {("603103444_1", "73474895_0")})["summary"]
    assert f2["length_change_over_0.1m"]["connections"] == {"603103444_2>73474895_0": 0.332,
                                                            "603103444_3>73474895_1": 0.332,
                                                            "603103444_4>73474895_2": 0.332}
    assert f2["reshaped_over_tolerance"]["count"] == 0 and f2["attribute_changes"]["max_numeric_change"] < 1e-6
    assert f2["right_of_way_op_pairs"] == ["603103444_1>73474895_0"]
    assert f2["right_of_way_changed_beyond_op_pairs"] == []


def test_justify_merges_takes_out_only_the_exact_declared_merge():
    rows = [{"id": "audit:J0:connection_mode:destination_lane_merge:W_in|N_out|2:1,2", "source": "connection_mode_audit",
             "finding": "connection_mode:destination_lane_merge:W_in|N_out|2:1,2", "anchor": "junction:J0"},
            {"id": "audit:J0:connection_mode:destination_lane_merge:W_in|N_out|2:0,1,2",
             "source": "connection_mode_audit", "finding": "connection_mode:destination_lane_merge:W_in|N_out|2:0,1,2",
             "anchor": "junction:J0"},
            {"id": "request:J0:asymmetric_foes:x", "source": "request_matrix", "finding": "asymmetric_foes",
             "anchor": "junction:J0"}]
    kept, justified = dt.justify_merges(rows, MERGE)
    assert [row["id"] for row in justified] == [rows[0]["id"]] and kept == rows[1:]
    assert justified[0]["evidence"]["label"].endswith("no field evidence")
    assert dt.justify_merges(rows, None) == (rows, [])


def test_int_findings_feeds_declared_merges_into_a_separate_list(monkeypatch):
    row = {"id": "audit:J0:connection_mode:destination_lane_merge:W_in|N_out|2:1,2", "source": "connection_mode_audit",
           "finding": "connection_mode:destination_lane_merge:W_in|N_out|2:1,2", "anchor": "junction:J0"}
    monkeypatch.setattr(build_profile, "_int_rows", lambda root, junctions: ([row], {"findings": []}))
    justified: list = []
    kept, _ = build_profile.int_findings(None, ["J0"], evidence=MERGE, justified=justified)
    assert kept == [] and [entry["id"] for entry in justified] == [row["id"]]
    assert build_profile.int_findings(None, ["J0"])[0] == [row]


def test_goal_create_freezes_the_delta_on_the_source_facts(tmp_path):
    source = _x4()
    source_file, before, after = _tables(tmp_path, source, _without_link(source, "W_in_1", "E_out_1"))
    entries = dt.derive_targets(before, after, edge_to=_edge_to(source), junctions={"J0"})
    declared = {"entries": entries, "derived_from": _derived(DELETE),
                "control": {"parent_control": {"path": "control.add.xml", "sha256": "0" * 64},
                            "removed_links": [{"pair": ["W_in_1", "E_out_1"], "tls_id": "J0"}], "rule": "test"}}
    kwargs = dict(net=source_file, site_id="x4", target_date="2026-10-26", mutable_junction_ids=["J0"],
                  mutable_edge_ids=SCOPE["mutable_edge_ids"], ops=["delete_lane_connection"], seeds=[42],
                  scratch_dir=tmp_path)
    created, payloads, summary = goal.create_build_goal(ROOT, declared=declared, **kwargs)
    again, _, _ = goal.create_build_goal(ROOT, declared=declared, **kwargs)
    assert created.to_json_bytes() == again.to_json_bytes()
    block = created.reference.declared_targets
    assert block.tag == dt.TAG and block.mode == "explore" and len(block.entries) == len(entries) == 12
    assert block.evidence_justified_destination_merges == {}
    assert created.reference.producer.endswith("of the goal source + declared target delta (fragility_fix)")
    assert created.int_baseline.basis == "source" and created.reference.reference_net is None
    assert summary["source_V"] == [1, 10, 0, 0, 0.0]
    assert summary["declared_targets"]["scored"] == 11 and len(summary["declared_targets"]["source_mismatched"]) == 11
    table = json.loads(payloads[created.reference.facts])
    assert "pair:W_in_1>E_out_1:signal_group" not in {fact["id"] for fact in table["facts"]}
    assert goal.lint_build_goal(json.loads(created.to_json_bytes())) == []
    with pytest.raises(ValueError, match="neither --facts nor --reference-net"):
        goal.create_build_goal(ROOT, declared=declared, reference_net=source_file, **kwargs)
    document = json.loads(created.to_json_bytes())
    document["reference"]["declared_targets"]["entries"][0]["anchor"] = "junction:W"
    assert any("outside the scope" in error for error in goal.lint_build_goal(document))
    plain, _, _ = goal.create_build_goal(ROOT, reference_net=source_file, **kwargs)
    assert "declared_targets" not in json.loads(plain.to_json_bytes())["reference"]


@pytest.mark.skipif(not (STORE / "gate/F2/step1/profile/profile.full.json").is_file(),
                    reason="the fragility store E:\\twin-store\\fragility has no F2 gate profile")
def test_the_real_f2_profile_justifies_its_declared_merge():
    profile = json.loads((STORE / "gate/F2/step1/profile/profile.full.json").read_text(encoding="utf-8"))
    audit = next(check for check in profile["invariants"] if check["id"] == "INV-AUDIT")
    justified = profile["gate"]["justified_merges"]["candidate"]
    assert audit["pass"] and [row["finding"] for row in justified] == [
        "connection_mode:destination_lane_merge:603103444|73474895|0:1,2"]
    assert justified[0]["evidence"]["label"] == "model-justified (simulation fragility S01), no field evidence"
    assert profile["gate"]["G0"] == "pass" and profile["V"]["cand"] == [0, 0, 0, 0, 0.0]
    assert profile["source_dirty"] is True and "Declared targets (fragility_fix)" in profile["claim"]
    assert "V = 0 holds by construction" in profile["claim"] and "twin-layer exemption" in profile["claim"]
