"""Build known answers (M3): the K1 X4 harness, the K2 Ring1 harness on one fault, and the INT integrity sources."""

from __future__ import annotations

import copy
import importlib.util
import io
import json
import shutil
import sys
import xml.etree.ElementTree as ET
from contextlib import redirect_stdout
from pathlib import Path

import pytest

from astra_sumo.corridor.canonicalizer import canonicalize_raw_network
from astra_sumo.corridor.conflict_graph import audit_independent_movement_safety
from astra_sumo.corridor.enums import TrafficSide
from astra_sumo.corridor.netxml import parse_net_xml
from astra_sumo.corridor.synthetic_networks import apply_synthetic_mutation, build_synthetic_fixture
from astra_sumo.twin import invariants, junction_integrity, known_answers

ROOT = Path(__file__).resolve().parents[1]
VERIFIER = {"commit": "0" * 40, "clean": True}


def _x4(mutation: str | None = None, side: TrafficSide = TrafficSide.RIGHT) -> ET.Element:
    root = build_synthetic_fixture("standard-x4", traffic_side=side)
    return apply_synthetic_mutation(root, mutation) if mutation else root


def test_k1_attributes_every_x4_mutation_and_finds_wrong_turn(tmp_path: Path) -> None:
    report = known_answers.run_k1(tmp_path / "k1", repository_root=ROOT, verifier=VERIFIER)
    assert report["status"] == "pass" and report["failed_checks"] == []
    assert report["groups"]["fact_observable"] == sorted(known_answers.DESIGN_FACT_OBSERVABLE)
    assert report["groups"]["direction"] == ["wrong-turn"]
    assert len(report["groups"]["internal"]) == 14
    # Review finding: junction-level naming is automatic on X4's single junction, so (a) is scored at lane level,
    # where INT rows name the lanes their findings refer to, and must beat the null predictors.
    assert report["rates"]["top1_lanes"] == {"hits": 16, "cases": 16, "rate": 1.0}
    assert report["rates"]["top3_lanes"]["rate"] == 1.0
    assert report["rates"]["top1_junction_level"]["rate"] == 1.0
    null = report["null_baselines"]
    assert null["constant_1"] == 0.5 and null["constant"] <= 0.5 and null["cross_case"] < 0.5
    assert report["checks"]["top1_lane_rate_beats_every_null_predictor"]
    assert all(case["top"][0]["lanes"] for case in report["cases"] if case["fact_observable"])
    assert report["wrong_turn"]["nema_also_blocks"] == {"left": True, "right": True}
    assert all(case["direction_findings"] == ["dir:W_in_0>S_out_0"] for case in report["wrong_turn"]["cases"])
    sources = report["int_sources_by_mutation"]
    assert sources["asymmetric-foes.standard-x4.right"] == {"request_matrix": 1}
    assert sources["request-bit-order.standard-x4.left"] == {"request_matrix": 2}
    for mutation in ("protected-green-conflict", "link-index-2"):
        assert sources[f"{mutation}.standard-x4.right"] == {"independent_safety": 1}
    assert sources["pedestrian-conflict.pedestrian-x4.right"] == {"independent_safety": 1}
    assert all(row["findings"] == 0 for row in report["clean_direction"]) and len(report["clean_direction"]) == 24
    assert all(not row["hotspots"] and row["int"] == 0 for row in report["clean_controls"])
    stored = (tmp_path / "k1" / "k1-report.json").read_bytes()
    assert b"timing" not in stored and str(tmp_path).encode() not in stored.replace(b"\\\\", b"\\")


def test_k1_report_is_byte_identical_across_output_directories(tmp_path: Path) -> None:
    names = ("wrong-to-lane", "wrong-turn", "asymmetric-foes")
    for run in ("a", "b"):
        known_answers.run_k1(tmp_path / run, repository_root=ROOT, verifier=VERIFIER, sides=("right",),
                             mutations=names)
    first, second = ((tmp_path / run / "k1-report.json").read_bytes() for run in ("a", "b"))
    assert first == second
    with pytest.raises(FileExistsError):
        known_answers.run_k1(tmp_path / "a", repository_root=ROOT, verifier=VERIFIER, mutations=names)


def test_mutation_truth_names_the_changed_connection_and_its_link() -> None:
    clean = _x4()
    truth = known_answers.mutation_truth(clean, apply_synthetic_mutation(clean, "wrong-to-lane"))
    assert {"junction:J0", "tls:J0#1", "lane:W_in_1", "lane:E_out_0", "lane:E_out_1"} <= set(truth["anchors"])
    assert truth["junctions"] == ["J0"] and "lane:W_in_0" not in truth["anchors"]
    program = known_answers.mutation_truth(clean, apply_synthetic_mutation(clean, "protected-green-conflict"))
    assert program["anchors"] == ["junction:J0", "tls:J0"] and program["lanes"] == []
    assert known_answers.anchor_named("tls:J0#7", program) and not known_answers.anchor_named("edge:W_in", program)
    hotspot = {"rows": [{"id": "pair:W_in_1>E_out_1:signal_group"}, {"id": "audit:J0:connection_mode:x:1"}]}
    assert known_answers.lanes_named(hotspot, truth)
    assert not known_answers.lanes_named({"rows": [{"id": "audit:J0:connection_mode:x:1"}]}, truth)


def test_request_matrix_names_links_by_their_connection() -> None:
    assert junction_integrity.request_matrix_findings(_x4(), ["J0"]) == []
    asymmetric = junction_integrity.request_matrix_findings(_x4("asymmetric-foes"), ["J0"])
    assert [row["id"] for row in asymmetric] == ["request:J0:asymmetric_foes:W_in_0>S_out_0|W_in_1>E_out_1"]
    swapped = junction_integrity.request_matrix_findings(_x4("request-bit-order"), ["J0"])
    assert [row["id"] for row in swapped] == ["request:J0:self_foe:W_in_0>S_out_0", "request:J0:self_foe:W_in_1>E_out_1"]
    # A bitstring of the wrong length is the connection-mode audit's finding, not a second one here.
    assert junction_integrity.request_matrix_findings(_x4("request-length"), ["J0"]) == []
    assert junction_integrity.request_matrix_findings(_x4("asymmetric-foes"), ["elsewhere"]) == []
    root = _x4()
    root.find("junction/request[@index='3']").set("index", "30")
    assert [row["id"] for row in junction_integrity.request_matrix_findings(root, ["J0"])] == ["request:J0:index_set:12"]
    responding = _x4()
    responding.find("junction/request[@index='2']").set("response", "000000001000")
    assert [row["id"] for row in junction_integrity.request_matrix_findings(responding, ["J0"])] == [
        "request:J0:response_not_foe:W_in_2>N_out_2|E_in_0>N_out_0"]


def test_audit_identity_drops_connection_order_and_measured_lengths() -> None:
    keys = ["A_0>B_0", "A_1>B_1"]
    assert junction_integrity.audit_identity("connection_mode:path_endpoint_gap:1:exit:1.589m", keys) == \
        "connection_mode:path_endpoint_gap:A_1>B_1:exit"
    assert junction_integrity.audit_identity("connection_mode:internal_path_target_mismatch:0:1:B:0", keys) == \
        "connection_mode:internal_path_target_mismatch:A_0>B_0:A_1>B_1:B:0"
    assert junction_integrity.audit_identity("tls_link_binding:link_index_out_of_program_state:9:J:x:3", keys) == \
        "tls_link_binding:link_index_out_of_program_state:#9:J:x:3"
    unchanged = "connection_mode:turn_lane_order_inversion:W_in:s>l"
    assert junction_integrity.audit_identity(unchanged, keys) == unchanged


def test_safety_finding_ids_survive_a_geometry_edit_at_the_junction() -> None:
    conflicted = _x4("protected-green-conflict")
    before = junction_integrity.safety_findings(conflicted, ["J0"])
    assert [row["id"].split(":")[2] for row in before] == ["protected_green_movement_conflict"]
    widened = copy.deepcopy(conflicted)
    for lane in widened.iter("lane"):
        if lane.get("id") == "W_in_1":
            lane.set("width", "3.6")
    # The canonical ids hash lane widths, so the raw finding id changes; the semantic identity does not.
    def raw(root: ET.Element) -> list[str]:
        snapshot = canonicalize_raw_network(parse_net_xml(root), traffic_side=TrafficSide.RIGHT)
        return [finding.finding_id for finding in audit_independent_movement_safety(snapshot).findings]

    assert raw(widened) != raw(conflicted)
    assert [row["id"] for row in junction_integrity.safety_findings(widened, ["J0"])] == [row["id"] for row in before]
    assert junction_integrity.safety_findings(_x4(), ["J0"]) == []
    assert junction_integrity.safety_findings(conflicted, []) == []


def test_a_permissive_pair_keeps_its_identity_when_its_roles_swap() -> None:
    # Found by the 100 m K2 scope: a width edit near LSA104_part0 swapped which of two permissive movements the
    # safety audit reported as permissive, so a pre-existing finding looked new.
    relation = {"allow_reverse": True, "directions": [{"yielding_movement_id": "b"}, {"yielding_movement_id": "a"}]}
    first = {"permissive_movement_id": "a", "conflicting_movement_id": "b", "phase_index": 0,
             "request_relation": relation}
    swapped = {**first, "permissive_movement_id": "b", "conflicting_movement_id": "a"}
    assert junction_integrity.role_free(first) == junction_integrity.role_free(swapped)
    assert junction_integrity.role_free(first)["movement_pair"] == ["a", "b"]
    one_way = {**first, "request_relation": {**relation, "allow_reverse": False}}
    assert junction_integrity.role_free(one_way) == one_way


def test_build_invariants_are_differential_against_parent_and_baseline() -> None:
    common = {"report_status": "measured", "candidate_sha256": "a", "declared_sha256": "a", "load_status": "pass",
              "source_immutable": True, "source_sha256": "p", "parent_sha256": "p"}
    gap = "J:connection_mode:path_endpoint_gap:A_0>B_0:exit"
    held = invariants.inv_materialize(structural_failures=1, structure={"parent": [(gap, 1.589)],
                                                                        "candidate": [(gap, 1.62)]}, **common)
    assert held["pass"] and "1 on the candidate, 1 on the parent" in held["reason"]
    new = invariants.inv_materialize(structural_failures=2, structure={"parent": [(gap, 1.5)],
                                                                       "candidate": [(gap, 1.5), (gap, 1.4)]}, **common)
    assert new["witness_ids"] == [f"structure:{gap}"]
    assert invariants.inv_materialize(structural_failures=3, structure={"parent": [], "candidate": [(gap, 0.2)]},
                                      **common)["witness_ids"] == [f"structure:{gap}", "structure:count:3"]
    # The structure-free fallback is gone: the differential check always needs both structure lists.
    with pytest.raises(TypeError):
        invariants.inv_materialize(structural_failures=0, **common)
    audit = invariants.inv_audit(["a"], ["a", "b", "c"], ["J"], baseline={"c": None})
    assert audit["witness_ids"] == ["b"] and "1 in the frozen INT baseline" in audit["reason"]


def test_a_pre_existing_measured_finding_that_grows_is_a_regression() -> None:
    # Review finding: identities drop measured lengths, so a 1.589 m gap that grew to 21.3 m passed INV-MATERIALIZE
    # and INV-AUDIT unchanged (clean Ring1 LSA535_part0).
    common = {"report_status": "measured", "candidate_sha256": "a", "declared_sha256": "a", "load_status": "pass",
              "source_immutable": True, "source_sha256": "p", "parent_sha256": "p"}
    assert junction_integrity.audit_measure("connection_mode:path_endpoint_gap:3:exit:21.305m") == 21.305
    assert junction_integrity.audit_measure("connection_mode:turn_lane_order_inversion:W_in:s>l") is None
    gap = "LSA535_part0:connection_mode:path_endpoint_gap:186821027#0_1>43305966#1_0:exit"
    parent = [(gap, 1.589), (gap, 2.188)]
    grown = invariants.inv_materialize(structural_failures=2, structure={"parent": parent,
                                                                         "candidate": [(gap, 21.305), (gap, 2.188)]},
                                       **common)
    assert grown["witness_ids"] == [f"worse:{gap}"]
    assert invariants.inv_materialize(structural_failures=2, structure={"parent": parent, "candidate": [
        (gap, 1.65), (gap, 2.188)]}, **common)["pass"]
    audit = invariants.inv_audit([("audit:J:g", 1.0)], [("audit:J:g", 1.2), ("audit:J:b", 3.0)], ["J"],
                                 baseline={"audit:J:b": 2.0})
    assert audit["witness_ids"] == ["worse:audit:J:b", "worse:audit:J:g"]


def test_int_rows_name_the_lanes_of_their_findings() -> None:
    root = _x4()
    index = junction_integrity.road_lane_index(root)
    assert junction_integrity.named_lanes([
        "audit:J0:connection_mode:incoming_motorized_lane_without_connection:W_in:0",
        "audit:J0:connection_mode:duplicate_direct_connection:W_in|S_out|0|0|:J0_0_0:2",
        "audit:J0:connection_mode:request_count_mismatch:12:11"], index) == ["S_out_0", "W_in_0"]
    assert junction_integrity.named_lanes(["request:J0:asymmetric_foes:W_in_0>S_out_0|W_in_1>E_out_1"], index) == [
        "E_out_1", "S_out_0", "W_in_0", "W_in_1"]
    assert junction_integrity.named_lanes(["W_in:7", ":J0_0_0", "J0"], index) == []
    # A safety finding names the lanes of the movements it references, not every lane of its controller.
    rows = junction_integrity.integrity_rows(_x4("shared-link-index-conflict"), ["J0"])
    assert rows and all({"S_in_1", "N_out_1"} <= set(row["lanes"]) and len(row["lanes"]) <= 4 for row in rows)
    assert known_answers.lanes_named({"rows": rows}, {"lanes": ["N_out_1", "S_in_1"]})


def test_null_baselines_and_the_k2_scope_and_timing_checks() -> None:
    def case(name, side, truth, top):
        return {"case": f"{name}.{side}", "mutation": name, "side": side, "truth": {"lanes": truth},
                "top": [{"lanes": top}]}

    cases = [case("a", "right", ["x", "y"], ["x"]), case("b", "right", ["x"], ["x"]), case("c", "right", ["z"], ["z"])]
    null = known_answers.null_baselines(cases)
    assert (null["constant_1"], null["k"], null["constant_lanes"]) == (round(2 / 3, 6), 1, ["x"])
    # a answered with b (hit), b with c (miss), c wraps to a (miss).
    assert null["cross_case"] == round(1 / 3, 6)
    clean = _x4()
    assert known_answers.neighbourhood(clean, ["W_in"], [], 10.0) == ["J0", "W"]
    assert set(known_answers.neighbourhood(clean, [], ["J0"], 200.0)) == set(known_answers.fixture_junctions(clean))
    # Review finding: (f) carried a limit that nothing compared; the timing checks now fail over the limit.
    assert known_answers.timing_checks({"perf.source_profile_s": 1.2, "perf.judged_profile_s": 2.4}) == {
        "perf_source_profile_within_2s": True, "perf_judged_profile_within_2s": False}
    assert not any(known_answers.timing_checks({}).values())


def test_k2_case_helpers_on_fabricated_profiles() -> None:
    clean = _x4()
    family = known_answers.injected_family(clean, ["W_in"], [])
    assert {"edge:W_in", "lane:W_in_0", "junction:J0", "junction:W", "tls:J0#"} <= set(family)
    row = {"id": "lane:W_in_1:width", "suggested_ops": [{"op": "set_lane_width", "complete": True,
                                                          "params": {"lane_id": "W_in_1", "width_m": 3.2}}]}
    profile = {"hotspots": [{"id": "H:INT:junction:J0", "class": "INT", "anchor": "junction:J0", "rows": []},
                            {"id": "H:ATTR:edge:W_in", "class": "ATTR", "anchor": "edge:W_in", "rows": [row]}],
               "hotspot_ids": ["H:INT:junction:J0", "H:ATTR:edge:W_in"], "escalations": [], "int_findings": []}
    expect = {"class": "ATTR", "anchor": "edge:W_in", "op": "set_lane_width"}
    assert known_answers.hotspot_checks(profile, expect, family)["ok"]
    assert known_answers.suggested_operation(profile, expect) == (
        {"operation": "set_lane_width", "params": {"lane_id": "W_in_1", "width_m": 3.2}}, "H:ATTR:edge:W_in")
    stray = {**profile, "hotspots": profile["hotspots"] + [{"id": "H:GEO:edge:N_in", "class": "GEO",
                                                            "anchor": "edge:N_in", "rows": []}]}
    assert known_answers.hotspot_checks(stray, expect, family)["outside_family"] == ["H:GEO:edge:N_in"]
    opx = {"hotspots": [], "hotspot_ids": [], "int_findings": [],
           "escalations": [{"id": "H:OPX:lane:W_in_1", "facts": [{"reason": "op_not_expressible"}]}]}
    assert known_answers.hotspot_checks(opx, {"escalation": "H:OPX:lane:W_in_1"}, family)["ok"]
    assert not known_answers.hotspot_checks(opx, {"escalation": "H:OPX:lane:W_in_2"}, family)["ok"]


def test_raw_perturbations_touch_one_lane_only() -> None:
    clean = _x4()
    widened = known_answers.perturb_raw(clean, {"kind": "width", "lane_id": "W_in_1", "width_m": 2.6})
    assert next(lane for lane in widened.iter("lane") if lane.get("id") == "W_in_1").get("width") == "2.60"
    shifted = known_answers.perturb_raw(clean, {"kind": "shift", "lane_id": "W_in_1", "offset_m": 1.0})
    lanes = {lane.get("id"): lane.get("shape") for lane in shifted.iter("lane")}
    original = {lane.get("id"): lane.get("shape") for lane in clean.iter("lane")}
    assert [lane for lane in lanes if lanes[lane] != original[lane]] == ["W_in_1"]
    with pytest.raises(ValueError, match="no interior vertex"):
        known_answers.perturb_raw(clean, {"kind": "jitter", "lane_id": "W_in_1", "offset_m": 1.0})
    with pytest.raises(ValueError, match="not in the net"):
        known_answers.perturb_raw(clean, {"kind": "width", "lane_id": "nowhere", "width_m": 2.6})


@pytest.mark.skipif(not shutil.which("netconvert") or not shutil.which("sumo"), reason="SUMO binaries required")
def test_k2_one_ledger_fault_reaches_v0_through_its_suggested_inverse(tmp_path: Path) -> None:
    report = known_answers.run_k2(tmp_path / "k2", repository_root=ROOT, verifier=VERIFIER,
                                  netconvert_binary=shutil.which("netconvert"), case_ids=("speed",), perf=False)
    assert report["status"] == "pass", report["failed_checks"]
    assert report["net"]["sha256"].startswith(known_answers.RING1_SHA256_PREFIX)
    direction = report["ring1_direction"]
    assert (direction["rule"], direction["checked_count"], direction["finding_count"], direction["ambiguous_count"]) \
        == ("sumo_edge_node_angle", 963, 0, 25)
    assert direction["baseline_sha256"] == "0319978d4c02f9c75a07afc6c3890110c867214a289e6faedc632d29cafabc87"
    assert report["clean_control"]["hotspots"] == [] and report["clean_control"]["V"] == [0, 0, 0, 0, 0.0]
    (case,) = report["cases"]
    assert case["fault"]["outside_fact_changes"] == [] and case["fault"]["label"] == known_answers.LEDGER_LABEL
    assert case["source"]["hotspot_ids"] == ["H:ATTR:edge:-186713427"]
    assert case["source"]["target"]["op"] == "set_speed"
    (step,) = case["steps"]
    assert step["basis"] == "suggested_op of H:ATTR:edge:-186713427"
    assert step["V"] == [0, 0, 0, 0, 0.0] and step["G0"] == "pass" and step["outside_fact_changes"] == []
    assert step["screening"] == "dynamic_not_run"
    assert "-186713427" in case["scope"]["edges"] and len(case["scope"]["junctions_in_scope"]) > 2
    stored = json.loads((tmp_path / "k2" / "k2-report.json").read_text(encoding="utf-8"))
    assert "timing" not in stored and stored["routeability"].startswith("not run")
    assert report["timing_checks"] == {} and stored["timing_checks"] == "not run (--no-perf)"


def test_k2_cli_blocks_when_a_timing_check_fails(tmp_path: Path, monkeypatch) -> None:
    # Review finding: the judged (f) profile ran over its 2 s limit while K2 reported pass and the CLI exited 0.
    monkeypatch.setattr(sys, "dont_write_bytecode", sys.dont_write_bytecode)
    spec = importlib.util.spec_from_file_location("astra_twin_cli_k2", ROOT / "plugins/astra-sumo/scripts/astra_twin.py")
    cli = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cli)

    def fake_run_k2(out: Path, **_) -> dict:
        out.mkdir()
        (out / "k2-report.json").write_bytes(b"{}\n")
        return {"status": "pass", "failed_checks": [], "cases": [], "perf_internal_paths": {"limit_s": 2.0},
                "ring1_direction": {key: None for key in ("rule", "checked_count", "finding_count", "ambiguous_count",
                                                           "baseline_sha256")},
                "timing": {"perf.judged_profile_s": 2.4},
                "timing_checks": {"perf_source_profile_within_2s": True, "perf_judged_profile_within_2s": False}}

    monkeypatch.setattr(cli.known_answers, "run_k2", fake_run_k2)
    stream = io.StringIO()
    with redirect_stdout(stream):
        code = cli.main(["k2", "run", "--out", str(tmp_path / "k2"), "--json"])
    payload = json.loads(stream.getvalue())
    assert (code, payload["status"], payload["report"]["status"]) == (3, "blocked", "pass")
    assert payload["failed_timing_checks"] == ["perf_judged_profile_within_2s"]
