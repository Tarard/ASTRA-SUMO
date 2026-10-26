"""Build profile (M3): per-kind acceptance, goal lint, V materiality, G0 exploits, the L3 hard rule and the views."""

from __future__ import annotations

import copy
import hashlib
import importlib.util
import io
import json
import math
import shutil
import sys
import xml.etree.ElementTree as ET
from contextlib import redirect_stdout
from pathlib import Path

import pytest
from pydantic import ValidationError

from astra_sumo.core.network_edit_operations import _remap_lane_count_facts, check_lane_artifact_positions
from astra_sumo.core.reference_net_facts import ReferenceFactParameters, build_reference_net_facts
from astra_sumo.corridor.enums import TrafficSide
from astra_sumo.corridor.synthetic_networks import apply_synthetic_mutation, build_synthetic_fixture
from astra_sumo.twin import (build_gate, build_profile, direction_check, goal, ids, invariants, junction_integrity,
                             movement_guard, render, signal_partition)

ROOT = Path(__file__).resolve().parents[1]
RING1 = ROOT / "benchmarks/hamburg_ring1_119_control_diagnosis_20261022_v1/nominal-inputs/network.net.xml"
LOCATION = '<location netOffset="0.00,0.00" convBoundary="-100,-100,100,100" origBoundary="-100,-100,100,100" ' \
           'projParameter="!"/>'
VERIFIER = {"commit": "0" * 40, "clean": True}


def _x4(mutation: str | None = None) -> ET.Element:
    root = build_synthetic_fixture("standard-x4", traffic_side=TrafficSide.RIGHT)
    root = apply_synthetic_mutation(root, mutation) if mutation else root
    root.insert(0, ET.fromstring(LOCATION))
    return root


def _write(root: ET.Element, path: Path) -> Path:
    path.write_bytes(ET.tostring(root, encoding="utf-8", xml_declaration=True))
    return path


def _lane(root: ET.Element, lane_id: str) -> ET.Element:
    return next(lane for lane in root.iter("lane") if lane.get("id") == lane_id)


def _goal(tmp_path: Path, source: ET.Element, *, reference: ET.Element | None = None, facts: dict | None = None,
          **kwargs) -> tuple[goal.BuildGoal, bytes, bytes, Path, Path]:
    tmp_path.mkdir(parents=True, exist_ok=True)
    net = _write(source, tmp_path / "source.net.xml")
    clean = _write(reference if reference is not None else _x4(), tmp_path / "reference.net.xml")
    facts_file = None
    if facts is not None:
        facts_file = tmp_path / "facts.json"
        facts_file.write_bytes(json.dumps(facts).encode("utf-8"))
    created, table, _ = goal.create_build_goal(ROOT, net=net, reference_net=clean, facts=facts_file,
                                               target_date="2026-10-25", site_id="x4",
                                               **{"mutable_junction_ids": ["J0"], "mutable_edge_ids": ["W_in"],
                                                  "seeds": [42], **kwargs})
    path, _, _ = goal.write_build_goal(created, table, tmp_path / "store")
    loaded, data, facts_bytes, facts_path = goal.load_build_goal(path)
    return loaded, data, facts_bytes, facts_path, net


def _profile(built, **kwargs) -> dict:
    loaded, data, facts_bytes, facts_path, _ = built
    return build_profile.build_build_profile(loaded, data, facts_bytes, facts_path=facts_path, repository_root=ROOT,
                                             verifier=VERIFIER, **kwargs)[0]


def _facts_for(tmp_path: Path, root: ET.Element) -> dict:
    net = _write(root, tmp_path / "facts-reference.net.xml")
    return build_reference_net_facts(net, site_id="x4", target_date="2026-10-25")[0]


def _passing_audit(trip_sha256: str, *, frozen: bool) -> dict:
    """A clean routeability audit in the real report shape, on the given (goal-frozen) trips."""
    return {"status": "pass", "routeability_status": "pass", "route_generation": {"status": "pass"},
            "od_policy": {"frozen_trip_sha256" if frozen else "request_file_sha256": trip_sha256},
            "final_attempt": {"vehicle_routes": {"failures": [], "missing_vehicle_ids": []},
                              "inspection": {"summary": {"collisions": 0, "teleports": 0}}}}


def _fake_smoke(broken: tuple[tuple[str, str], ...] = (), on: Path | None = None):
    """A movement-guard smoke without SUMO: every direct movement of the junction is driven and arrives, except the
    ``broken`` lane pairs on the net ``on`` (every net when None)."""
    def smoke(net: Path, junction: str, pairs: list, out: Path) -> dict:
        root = ET.parse(net).getroot()
        ends = {edge.get("id"): (edge.get("from"), edge.get("to")) for edge in root.findall("edge")
                if edge.get("function") != "internal"}
        driven = sorted([f"{row.get('from')}_{row.get('fromLane')}", f"{row.get('to')}_{row.get('toLane')}"]
                        for row in root.findall("connection")
                        if ends.get(row.get("from"), (None, None))[1] == junction
                        and ends.get(row.get("to"), (None, None))[0] == junction)
        bad = set(broken) if on is None or Path(net).resolve() == Path(on).resolve() else set()
        return {"status": "ran", "run_ok": True, "driven": driven,
                "arrived": [pair for pair in driven if tuple(pair) not in bad], "skipped": [],
                "events": {key: 0 for key in movement_guard.EVENT_KEYS}}
    return smoke


def file_sha(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _judge(tmp_path: Path, built, candidate: ET.Element, *, operation: dict | None = None,
           artifacts: list[Path] | None = None, affected: tuple[str, ...] = (), parent_net: Path | None = None,
           parent_profile: Path | None = None, out: Path | None = None, seeds: bool = False,
           structural: int = 0, l2: bool = False, broken: tuple[tuple[str, str], ...] = (),
           plan: dict | None = None, everywhere: bool = False, smoke=None) -> dict:
    """Profile ``candidate`` through a fabricated verify-edit report whose construction checks all pass, so only the
    twin's own G0, V, L2 and L3 decide (no SUMO). ``seeds`` adds clean routeability audits on the goal's frozen trips;
    ``l2`` runs the real movement guard with a fake smoke (``broken`` lane pairs fail on the candidate, or on both
    nets with ``everywhere``; ``smoke`` replaces that fake smoke); ``out`` writes
    the profile directory (``profile.full.json``) instead of returning it in memory. Like ``run_verify_edit``, the
    report is made with the parent's effective fact table, and a ``set_lane_count`` op records verify-edit's own
    selector remap of it under ``plan["lane_index_mapping"]``."""
    loaded, data, facts_bytes, facts_path, source = built
    parent = parent_net or source
    work = tmp_path / f"judge-{len(list(tmp_path.glob('judge-*')))}"
    work.mkdir()
    net = _write(candidate, work / "candidate.net.xml")
    operation = operation or {"id": "op", "operation": "set_edge_shape",
                              "params": {"edge_id": "W_in", "shape": [[-100, 0], [-5, 0]]}}
    ledger = work / "ledger.json"
    ledger.write_bytes(json.dumps({"source_net_file": str(parent), "operations": [operation]}).encode("utf-8"))
    plan_doc = {"mutable_junction_ids": ["J0", "W"], "mutable_edge_ids": ["W_in"], **(plan or {}),
                "operation": operation, "source": {"path": str(parent)}}
    plan_file = work / "plan.json"
    plan_file.write_bytes(json.dumps(plan_doc).encode("utf-8"))
    verify_facts = facts_path
    if parent_profile is not None:
        recorded = json.loads(Path(parent_profile).read_text(encoding="utf-8"))
        if recorded["subject"] == "candidate":
            verify_facts = Path(recorded["inputs"]["effective_facts"]["path"])
    scorecard = work / "source-scorecard.json"
    scorecard.write_bytes(json.dumps({"inputs": {str(verify_facts): file_sha(verify_facts)}}).encode())
    lane_artifacts = {"status": "not_applicable", "affected_lane_ids": list(affected)}
    if artifacts:
        lane_artifacts.update(check_lane_artifact_positions(candidate, artifacts, affected))
    sha = hashlib.sha256(net.read_bytes()).hexdigest()
    trips = [row for row in loaded.guards.routeability.trips if row.sha256] if seeds else []
    report = {"status": "measured", "candidate_net_file": str(net), "candidate_sha256": sha,
              "source": {"path": str(parent), "sha256": hashlib.sha256(parent.read_bytes()).hexdigest()},
              "ledger": {"path": str(ledger), "sha256": hashlib.sha256(ledger.read_bytes()).hexdigest()},
              "plan_file": str(plan_file), "source_immutable": True, "source_scorecard": str(scorecard),
              "outside_fact_changes": [], "routeability_seeds": [row.seed for row in trips],
              "routeability_by_seed": [{"seed": row.seed, "source_routeability": _passing_audit(row.sha256, frozen=False),
                                        "candidate_routeability": _passing_audit(row.sha256, frozen=True)}
                                       for row in trips],
              "construction": {"structural_failure_count": structural, "sumo_load": {"status": "pass"},
                               "source_immutable": True, "new_protected_green_conflicts": [],
                               "lane_coordinate_artifacts": lane_artifacts}}
    if operation["operation"] == "set_lane_count":
        report["fact_selector_remap"] = _remap_lane_count_facts(verify_facts, plan_doc, work / "candidate-facts.json")
    (work / "edit-verification.json").write_bytes(json.dumps(report).encode("utf-8"))
    guard = None
    if l2:
        guard = work / "movement-guard"
        build_profile.run_movement_guard(loaded, data, facts_bytes, facts_path=facts_path, verify_edit=work,
                                         output_dir=guard, verifier=VERIFIER, repository_root=ROOT,
                                         parent_profile=parent_profile,
                                         smoke=smoke or _fake_smoke(broken, None if everywhere else net))
    if out is not None:
        return build_profile.write_build_profile(loaded, data, facts_bytes, facts_path=facts_path, repository_root=ROOT,
                                                 out_dir=out, verifier=VERIFIER, verify_edit=work,
                                                 parent_profile=parent_profile, movement_guard_record=guard)
    return _profile(built, verify_edit=work, parent_profile=parent_profile, movement_guard_record=guard)


def _failed(profile: dict) -> dict[str, list[str]]:
    return {check["id"]: check["witness_ids"] for check in profile["invariants"] if not check["pass"]}


# ---- per-kind acceptance ------------------------------------------------------------------------------------------

def test_per_kind_acceptance_table_classes_and_excess_formulas():
    assert build_profile.KIND_CLASS == {
        "lane_count": "TOP", "movement_inventory": "TOP", "lane_permissions": "TOP", "stop_line": "CTL",
        "signal_group": "CTL", "lane_centerline": "GEO", "junction_surface": "GEO", "speed": "ATTR",
        "lane_width": "ATTR"}
    assert set(build_profile.ACCEPT) == set(build_profile.KIND_CLASS)

    def excess(entry, metrics):
        return {name: part["excess"] for name, part in build_profile.components(entry, metrics).items()}

    stop = {"kind": "stop_line", "tol_eff": 0.5, "stop_line_mode": "longitudinal"}
    assert excess(stop, {"longitudinal_offset_m": -1.75, "offset_m": 9}) == {"stop_line_m": pytest.approx(2.5)}
    assert excess({**stop, "stop_line_mode": "distance"}, {"offset_m": 0.4}) == {"stop_line_m": 0.0}
    line = {"kind": "lane_centerline", "tol_eff": 0.5, "minimum_coverage": 0.95}
    metrics = {"max_sampled_lateral_offset_m": 0.8, "coverage_within_m": {"0.5": 0.85}, "coverage_sample_count": 100,
               "mean_lateral_offset_m": 0.3}
    assert excess(line, metrics) == {"max_lateral_m": pytest.approx(0.6), "coverage_shortfall": pytest.approx(2.0)}
    # With minimum_coverage 1 the coverage scale is one sample; a declared maximum mean adds its own component.
    strict = {**line, "minimum_coverage": 1.0, "maximum_mean_m": 0.2}
    assert excess(strict, metrics) == {"max_lateral_m": pytest.approx(0.6), "coverage_shortfall": pytest.approx(15),
                                       "mean_lateral_m": pytest.approx(0.2)}
    unmeasured = build_profile.components(line, {"coverage_within_m": {"0.5": 0.0}, "coverage_sample_count": 100})
    assert unmeasured["max_lateral_m"]["value"] is None
    assert unmeasured["coverage_shortfall"]["excess"] == pytest.approx(19)
    assert excess({"kind": "lane_width", "tol_eff": 0.1}, {"absolute_width_error_m": 0.3}) == {
        "width_error_m": pytest.approx(2.0)}
    assert excess({"kind": "speed", "tol_eff": 0.1}, {"absolute_speed_error_m_s": 0.05}) == {"speed_error_m_s": 0.0}
    assert excess({"kind": "junction_surface", "minimum_iou": 0.9}, {"intersection_over_union": 0.85}) == {
        "iou_shortfall": pytest.approx(0.5)}
    for kind in ("lane_count", "lane_permissions", "movement_inventory", "signal_group"):
        assert build_profile.components({"kind": kind}, {"absolute_count_error": 2}) == {}
    row = build_profile.fact_row({"id": "f", "kind": "movement_inventory", "class": "TOP", "anchor": "junction:J0"},
                                 {"status": "mismatched", "metrics": {"false_positive": 1, "false_negative": 2}})
    assert (row["excess"], row["magnitude"]) == (None, 3)


def test_per_kind_rows_on_a_real_scorecard_match_the_formulas(tmp_path):
    clean = _x4()
    moved = copy.deepcopy(clean)
    for lane in ("W_in_0", "W_in_1", "W_in_2"):
        points = [tuple(map(float, p.split(","))) for p in _lane(moved, lane).get("shape").split()]
        _lane(moved, lane).set("shape", " ".join(f"{x},{y + 0.6}" for x, y in points))
    _lane(moved, "E_in_1").set("shape", "100.0,0.0 6.0,0.0")
    _lane(moved, "E_out_2").set("speed", "10")
    _lane(moved, "S_in_0").set("width", "3.5")
    _lane(moved, "N_in_0").set("allow", "passenger bus")
    built = _goal(tmp_path, moved, reference=clean)
    profile = _profile(built)
    rows = {row["id"]: row for row in profile["rows"]}
    assert set(rows) == {"lane:W_in_0:centerline", "lane:W_in_1:centerline", "lane:W_in_2:centerline",
                         "lane:E_in_1:stop_line", "lane:E_out_2:speed", "lane:S_in_0:width",
                         "lane:N_in_0:permissions"}
    assert {row["class"] for row in rows.values()} == {"GEO", "CTL", "ATTR", "TOP"}
    assert rows["lane:E_in_1:stop_line"]["excess"] == pytest.approx(1.0)
    assert rows["lane:E_out_2:speed"]["excess"] == pytest.approx((13.89 - 10 - 0.1) / 0.1)
    assert rows["lane:S_in_0:width"]["excess"] == pytest.approx(2.0)
    assert rows["lane:N_in_0:permissions"]["excess"] is None and rows["lane:N_in_0:permissions"]["magnitude"] == 1
    centerline = rows["lane:W_in_1:centerline"]["components"]
    assert centerline["max_lateral_m"]["value"] == pytest.approx(0.6)
    assert centerline["coverage_shortfall"]["value"] == pytest.approx(1.0)
    assert profile["V"]["cand"] == [1, 1, 3, 2, pytest.approx(round(sum(
        row["excess"] for row in rows.values() if row["excess"] is not None), 9))]
    # Every matched scored fact has zero excess (None when categorical); every mismatched numeric one is positive.
    assert all(excess in (0, None) for status, excess in profile["facts"].values() if status == "matched")
    assert all(row["excess"] > 0 for row in rows.values() if row["kind"] in build_profile.NUMERIC_KINDS)
    ops = {row["id"]: row["suggested_ops"][0] for row in rows.values()}
    # The lane ends 1 m short of the reference stop line: endOffset cannot extend it, so the op is incomplete.
    assert ops["lane:E_in_1:stop_line"] == {"op": "move_stop_line", "params": {"lane_id": "E_in_1"},
                                            "complete": False,
                                            "note": "the reference stop line lies 1.000 m beyond the edge end; "
                                                    "end_offset_m >= 0"}
    assert ops["lane:E_out_2:speed"] == {"op": "set_speed", "params": {"lane_id": "E_out_2", "speed_m_s": 13.89},
                                         "complete": True}
    assert ops["lane:S_in_0:width"]["params"] == {"lane_id": "S_in_0", "width_m": 3.2}
    assert ops["lane:N_in_0:permissions"]["params"] == {"lane_id": "N_in_0", "allow": "passenger"}
    assert ops["lane:W_in_1:centerline"]["op"] == "set_edge_shape" and not ops["lane:W_in_1:centerline"]["complete"]


def test_stop_line_suggestion_is_current_end_offset_plus_longitudinal_offset(tmp_path):
    clean = _x4()
    longer = copy.deepcopy(clean)
    # As in the recorded 2349 trial: the lane runs 4.73 m past the reference stop line.
    _lane(longer, "W_in_1").set("shape", "-100.0,0.0 -0.27,0.0")
    _lane(longer, "W_in_1").set("endOffset", "0.5")
    profile = _profile(_goal(tmp_path, longer, reference=clean))
    row = next(row for row in profile["rows"] if row["id"] == "lane:W_in_1:stop_line")
    assert row["suggested_ops"] == [{"op": "move_stop_line", "params": {"lane_id": "W_in_1", "end_offset_m": 5.23},
                                     "complete": True, "note": "current endOffset 0.5 + longitudinal +4.730 m"}]
    assert row["est_le"] == {"class": "CTL", "count": -1, "excess": pytest.approx(-(4.73 - 0.5) / 0.5)}


def test_hotspots_group_by_anchor_and_sort_by_class_then_weight(tmp_path):
    faulted = _x4("wrong-to-lane")
    _lane(faulted, "E_out_2").set("speed", "10")
    _lane(faulted, "E_in_1").set("shape", "100.0,0.0 7.0,0.0")
    profile = _profile(_goal(tmp_path, faulted))
    ids = profile["hotspot_ids"]
    assert ids[0] == "H:INT:junction:J0"
    assert ids[1:] == ["H:TOP:junction:J0", "H:CTL:lane:E_in_1", "H:CTL:tls:J0#1", "H:ATTR:edge:E_out"]
    top = next(h for h in profile["hotspots"] if h["id"] == "H:TOP:junction:J0")
    ops = [op["op"] for op in top["rows"][0]["suggested_ops"]]
    assert ops == ["add_lane_connection", "delete_lane_connection"]
    assert top["rows"][0]["suggested_ops"][0]["params"] == {"from": "W_in", "fromLane": 1, "to": "E_out",
                                                           "toLane": 1}
    assert all(row.get("est_le", {}).get("count") == -1 for h in profile["hotspots"] if h["class"] != "INT"
               for row in h["rows"])


# ---- goal-create: frozen set, escalations, lint ------------------------------------------------------------------

def test_goal_freezes_classes_escalations_and_keeps_the_calibrate_goal_unchanged(tmp_path):
    clean = _x4()
    table = _facts_for(tmp_path, clean)
    by_id = {fact["id"]: fact for fact in table["facts"]}
    extra = [
        {**by_id["lane:W_in_2:stop_line"], "id": "unresolved-stop", "mapping_status": "unresolved",
         "mapping_confidence": "high"},
        {**by_id["lane:W_in_1:centerline"], "id": "medium-line", "mapping_confidence": "medium"},
        {**by_id["lane:W_in_1:centerline"], "id": "thin-margin", "mapping_confidence": "high",
         "mapping_status": "confirmed", "mapping_diagnostics": {"best_alternative_margin_m": 0.4}},
        {**by_id["lane:W_in_1:centerline"], "id": "shift-fits", "mapping_confidence": "low",
         "expected": _shape(clean, "W_in_0")},
        {**by_id["lane:W_in_1:centerline"], "id": "nothing-fits", "mapping_confidence": "low",
         "expected": [[-100, 40], [-5, 40]]},
        {**by_id["lane:W_in_0:speed"], "id": "old-source", "source_ids": ["old"]},
        {**by_id["lane:W_in_0:speed"], "id": "no-expected", "expected": None},
        {**by_id["lane:W_in_0:stop_line"], "id": "no-threshold", "tolerance_longitudinal_m": None},
        {**by_id["edges:W_in:lane_count:passenger"], "id": "unlocked-count", "mapping_confidence": "high"}]
    table["facts"] += extra
    table["sources"].append({"id": "old", "path": table["sources"][0]["path"], "sha256": None,
                             "source_date": "2020-01-01", "authority": "old survey", "license": "x"})
    built = _goal(tmp_path, clean, facts=table)
    frozen = built[0].frozen
    excluded = {key: (value.class_, value.reason) for key, value in frozen.excluded.items()}
    assert excluded == {"unresolved-stop": ("COR", "mapping_unresolved"),
                        "medium-line": ("COR", "mapping_confidence_medium_not_high"),
                        "thin-margin": ("COR", "index_shift_margin_below_max_1m_2sigma"),
                        "shift-fits": ("COR", "mapping_confidence_low"),
                        "nothing-fits": ("COR", "mapping_confidence_low"),
                        "old-source": ("SRC", "source_date_gap_2489d"),
                        "no-expected": ("UNK", "reference_fact_unknown"),
                        "no-threshold": ("CTL", "acceptance_threshold_not_declared"),
                        "unlocked-count": ("COR", "mapping_not_locked")}
    assert frozen.excluded["shift-fits"].note == build_profile.COR_TEXT
    assert [row["mapping"] for row in frozen.excluded["shift-fits"].probe if row["max_lateral_m"] is not None
            and row["max_lateral_m"] <= 0.5] == ["shift-1"]
    assert frozen.excluded["nothing-fits"].note == build_profile.COR_ALL_FAIL_TEXT
    assert len(frozen.scored_facts) == 164 and frozen.counts["scored_GEO"] == 24
    assert frozen.sha256 == build_profile.frozen_sha256(
        [row.model_dump(by_alias=True, exclude_none=True) for row in frozen.scored_facts],
        {key: value.model_dump(by_alias=True, exclude_none=True) for key, value in frozen.excluded.items()})
    profile = _profile(built)
    escalations = {row["id"]: row["facts"][0]["status"] for row in profile["escalations"]}
    assert escalations["H:COR:lane:W_in_2"] == build_profile.COR_TEXT
    assert "H:SRC:edge:W_in" in escalations and "H:UNK:edge:W_in" in escalations
    assert profile["V"]["cand"] == [0, 0, 0, 0, 0.0]


def _shape(root: ET.Element, lane_id: str) -> list[list[float]]:
    return [[float(v) for v in point.split(",")] for point in _lane(root, lane_id).get("shape").split()]


def test_a_mismatched_lane_centerline_with_a_matching_sibling_is_op_not_expressible(tmp_path):
    clean = _x4()
    moved = copy.deepcopy(clean)
    _lane(moved, "W_in_1").set("shape", "-100.0,0.6 -5.0,0.6")
    built = _goal(tmp_path, moved, reference=clean)
    assert built[0].frozen.excluded["lane:W_in_1:centerline"].class_ == "OPX"
    profile = _profile(built)
    opx = next(row for row in profile["escalations"] if row["class"] == "OPX")
    assert opx["id"] == "H:OPX:lane:W_in_1" and opx["facts"][0]["status"] == build_profile.OPX_TEXT
    assert "lane:W_in_1:centerline" not in profile["facts"]


def test_goal_lint_refuses_bad_targets_scope_and_shape(tmp_path):
    built = _goal(tmp_path, _x4())
    document = json.loads(built[1])
    assert goal.lint_build_goal(document) == []

    def lint(change) -> list[str]:
        copy_ = copy.deepcopy(document)
        change(copy_)
        return goal.lint_build_goal(copy_)

    def entry(document_, kind):
        return next(row for row in document_["frozen"]["scored_facts"] if row["kind"] == kind)

    assert any("without a tolerance" in e for e in lint(lambda d: entry(d, "stop_line").pop("tol_eff")))
    assert any("without a tolerance" in e for e in lint(lambda d: entry(d, "speed").update(tol_eff=0)))
    assert any("below max(2 sigma" in e for e in lint(lambda d: d["reference"]["sigma"].update(comb_m=0.3)))
    assert any("below max(2 sigma" in e for e in lint(lambda d: entry(d, "lane_width").update(tol_eff=0.005)))
    junction = {"id": "junction:J0:surface", "kind": "junction_surface", "class": "GEO", "accept": "IoU",
                "anchor": "junction:J0", "minimum_iou": 1.0}
    assert any("minimum_iou 1 >= 1" in e for e in lint(lambda d: d["frozen"]["scored_facts"].append(junction)))
    assert any("categorical target given a numeric tolerance" in e
               for e in lint(lambda d: entry(d, "lane_count").update(tol_eff=0.5)))
    assert any("frozen class COR" in e for e in lint(lambda d: entry(d, "stop_line").update({"class": "COR"})))
    assert any("scope with no target" in e for e in lint(lambda d: d["scope"].update(
        mutable_junction_ids=["nowhere"], mutable_edge_ids=[])))
    assert any("scope with no target" in e for e in lint(lambda d: d["scope"].update(
        mutable_junction_ids=[], mutable_edge_ids=[])))
    assert any("g0_shape" in e for e in lint(lambda d: d.pop("g0_shape")))
    with pytest.raises(ValidationError, match="g0_shape"):
        goal.BuildGoal.model_validate({key: value for key, value in document.items() if key != "g0_shape"})


def test_goal_create_refuses_zero_tolerances_sigma_floors_and_untargeted_scopes(tmp_path):
    clean = _x4()
    table = _facts_for(tmp_path, clean)
    zero = copy.deepcopy(table)
    next(f for f in zero["facts"] if f["kind"] == "stop_line")["tolerance_longitudinal_m"] = 0.0
    with pytest.raises(ValueError, match="lint failed.*without a tolerance"):
        _goal(tmp_path / "zero", clean, facts=zero)
    categorical = copy.deepcopy(table)
    next(f for f in categorical["facts"] if f["kind"] == "lane_count")["tolerance_m"] = 1.0
    with pytest.raises(ValueError, match="categorical target given a numeric tolerance"):
        _goal(tmp_path / "categorical", clean, facts=categorical)
    with pytest.raises(ValueError, match=r"below max\(2 sigma"):
        _goal(tmp_path / "sigma", clean, sigma_m=0.3)
    with pytest.raises(ValueError, match="scope with no target"):
        _goal(tmp_path / "scope", clean, mutable_junction_ids=[], mutable_edge_ids=["nowhere"])
    assert not (tmp_path / "zero" / "store").exists()


# ---- V and materiality ---------------------------------------------------------------------------------------------

def _rows(counts: dict[str, int], excess: list[float], values: dict[str, float] | None = None) -> dict:
    rows = {}
    for cls, n in counts.items():
        for index in range(n):
            rows[f"{cls}{index}"] = {"class": cls, "status": "mismatched", "excess": None, "components": {}}
    for index, value in enumerate(excess):
        rows[f"num{index}"] = {"class": "GEO", "status": "mismatched" if value else "matched", "excess": value,
                               "components": {"max_lateral_m": {"value": (values or {}).get(f"num{index}", value),
                                                                "scale": 0.5}}}
    return rows


def test_v_is_lexicographic_with_real_materiality_and_no_numeric_worsening():
    parent = _rows({"TOP": 1}, [2.0, 1.0])
    assert build_gate.v_vector(parent) == [1, 0, 2, 0, 3.0]
    fixed_top = _rows({}, [2.0, 1.0])
    assert build_gate.compare_v(parent, fixed_top)["verdict"] == "improved"
    assert build_gate.compare_v(parent, fixed_top)["tier"] == "n_TOP"
    small = _rows({"TOP": 1}, [1.97, 0.99])
    result = build_gate.compare_v(parent, small)
    assert (result["verdict"], result["excess_decrease"]) == ("not_material", 0.04)
    assert build_gate.compare_v(parent, _rows({"TOP": 1}, [1.95, 1.0]))["verdict"] == "improved"
    assert build_gate.compare_v(parent, copy.deepcopy(parent))["verdict"] == "tie"
    assert build_gate.compare_v(parent, _rows({"TOP": 1}, [2.0, 1.2]))["verdict"] == "not_improved"
    # A count or excess gain bought with one component worsening by more than 0.1 x scale is rejected.
    traded = _rows({}, [0.0, 2.9], {"num0": 0.0, "num1": 1.06})
    worse = build_gate.compare_v(parent, traded)
    assert worse["verdict"] == "worsened" and worse["worsened"][0]["component"] == "max_lateral_m"
    within = _rows({}, [0.0, 0.9], {"num0": 0.0, "num1": 1.04})
    assert build_gate.compare_v(parent, within)["verdict"] == "improved"


def test_v_rejects_a_still_mismatched_categorical_fact_that_gets_worse():
    # Review finding: one set_lane_count op could worsen |obs - exp| on a mismatched lane count while a centerline
    # excess fell, and V said improved because categorical facts had no worsening guard.
    def rows(magnitude: int, excess: float) -> dict:
        return {"count": {"class": "TOP", "status": "mismatched", "excess": None, "components": {},
                          "magnitude": magnitude},
                "line": {"class": "GEO", "status": "mismatched", "excess": excess,
                         "components": {"max_lateral_m": {"value": 0.5 + excess * 0.5, "scale": 0.5}}}}

    worse = build_gate.compare_v(rows(1, 2.0), rows(3, 1.8))
    assert worse["verdict"] == "worsened" and worse["worsened"] == [
        {"fact": "count", "component": "magnitude", "parent": 1, "candidate": 3, "limit": 0}]
    assert build_gate.compare_v(rows(1, 2.0), rows(1, 1.8))["verdict"] == "improved"


# ---- G0 exploits (fabricated verify-edit reports, no SUMO) --------------------------------------------------------

def test_an_unchanged_candidate_passes_g0_and_ties(tmp_path):
    built = _goal(tmp_path, _x4())
    profile = _judge(tmp_path, built, _x4())
    assert _failed(profile) == {} and profile["gate"]["G0"] == "pass"
    assert (profile["status"], profile["gate"]["screening"]) == ("no_effect", "v_tie")


def test_g0_rejects_a_lane_deletion(tmp_path):
    clean = _x4()
    table = _facts_for(tmp_path, clean)
    unresolved = {**next(f for f in table["facts"] if f["id"] == "lane:W_in_2:stop_line"), "id": "hidden-stop",
                  "mapping_status": "unresolved", "mapping_confidence": "high"}
    table["facts"].append(unresolved)
    built = _goal(tmp_path, clean, facts=table)
    candidate = copy.deepcopy(clean)
    edge = candidate.find("edge[@id='W_in']")
    edge.remove(_lane(candidate, "W_in_2"))
    for connection in candidate.findall("connection"):
        if connection.get("from") == "W_in" and connection.get("fromLane") == "2":
            candidate.remove(connection)
    profile = _judge(tmp_path, built, candidate)
    failed = _failed(profile)
    assert profile["status"] == "blocked" and profile["gate"]["screening"] == "g0_rejected"
    assert "regressed:lane:W_in_2:stop_line" in failed["G0-REGRESSED"]
    assert "component:lane:W_in_2:stop_line:stop_line_m" in failed["INV-MEASURABLE"]
    # The unresolved copy turns unknown on the deleted lane; it is a frozen COR escalation, never scored.
    assert built[0].frozen.excluded["hidden-stop"].class_ == "COR"
    escalated = next(row for row in profile["escalations"] if row["id"] == "H:COR:lane:W_in_2")
    assert escalated["facts"][0]["net_status"] == "unknown"


def test_g0_rejects_a_selector_breaking_value_error(tmp_path):
    clean = _x4()
    built = _goal(tmp_path, clean)
    candidate = copy.deepcopy(clean)
    candidate.append(ET.fromstring('<connection from="W_in" to="E_out" fromLane="7" toLane="1" dir="s"/>'))
    profile = _judge(tmp_path, built, candidate)
    lost = _failed(profile)["INV-MEASURABLE"]
    assert "lost:lanes:W_in_0:movement_inventory" in lost and "lost:pair:W_in_0>S_out_0:signal_group" in lost


def test_g0_rejects_lane_shortening_that_buys_a_smaller_lateral_offset(tmp_path):
    clean = _x4()
    bent = copy.deepcopy(clean)
    for lane in ("W_in_0", "W_in_1", "W_in_2"):
        (x0, y0), (x1, y1) = _shape(clean, lane)
        mid = (x1 - 10, y0 + (y1 - y0) * (x1 - 10 - x0) / (x1 - x0))
        _lane(bent, lane).set("shape", f"{x0},{y0} {mid[0]},{mid[1]} {x1},{y1 + 1.0}")
    built = _goal(tmp_path, bent, reference=clean, mutable_edge_ids=["W_in"])
    assert "lane:W_in_1:centerline" in {row.id for row in built[0].frozen.scored_facts}
    candidate = copy.deepcopy(bent)
    (x0, y0), (mx, my), _ = [tuple(p) for p in _shape(bent, "W_in_1")]
    _lane(candidate, "W_in_1").set("shape", f"{x0},{y0} {mx},{my}")
    profile = _judge(tmp_path, built, candidate)
    assert _failed(profile)["INV-COVERAGE"] == ["coverage:lane:W_in_1:centerline"]


def test_g0_rejects_a_zigzag_shape_beyond_g0_shape(tmp_path):
    clean = _x4()
    built = _goal(tmp_path, clean, mutable_edge_ids=["W_in"])
    candidate = copy.deepcopy(clean)
    # 0.4 m wiggles every 0.5 m stay inside the 0.5 m tolerance, so only the shape rule can see them.
    points = [(-100 + 0.5 * index, 0.4 if index % 2 else 0.0) for index in range(191)]
    _lane(candidate, "W_in_1").set("shape", " ".join(f"{x},{y}" for x, y in points))
    profile = _judge(tmp_path, built, candidate)
    shape = _failed(profile)["INV-SHAPE"]
    assert shape == ["heading:lane:W_in_1", "vertex_delta:lane:W_in_1"]
    assert "G0-REGRESSED" not in _failed(profile) and profile["gate"]["screening"] == "g0_rejected"


def test_g0_rejects_a_zigzag_next_to_an_existing_sharp_bend(tmp_path):
    # Review finding: the turn limit was relaxed to the parent lane's own largest turn, so on a lane with a 60 deg bend
    # a new 60 deg zigzag (vertex delta 5, excursion 1.2 m) passed.
    bent = _x4()
    _lane(bent, "W_in_1").set("shape", "-75.0,-43.30127 -50.0,0.0 -5.0,0.0")
    built = _goal(tmp_path, bent, reference=bent, mutable_edge_ids=["W_in"])
    zigzag = copy.deepcopy(bent)
    _lane(zigzag, "W_in_1").set("shape", "-75.0,-43.30127 -50.0,0.0 -40.0,0.0 -37.92,1.2 -35.84,0.0 -33.76,1.2 "
                                         "-31.68,0.0 -5.0,0.0")
    change = invariants.shape_change(_shape(bent, "W_in_1"), _shape(zigzag, "W_in_1"))
    assert change["max_turn_parent_deg"] == pytest.approx(60.0) and change["max_turn_candidate_deg"] < 60.5
    assert change["vertex_delta"] == 5 and change["directed_hausdorff_m"] == pytest.approx(1.2)
    zigzagged = _judge(tmp_path, built, zigzag)
    assert "heading:lane:W_in_1" in _failed(zigzagged)["INV-SHAPE"]
    # Decision D3: the parent's own 60 deg vertex (a clockwise turn, signed -60) is inherited there, but the new 60 deg
    # zigzag turns are bounded.
    shape = next(row for row in zigzagged["invariants"] if row["id"] == "INV-SHAPE")
    assert shape["inherited_bends"] == {"lane:W_in_1": [[-50.0, 0.0, pytest.approx(-60.0, abs=1e-4)]]}
    # The existing 60 deg bend re-drawn with one more collinear vertex is unchanged geometry: exempt, G0 passes.
    redrawn = copy.deepcopy(bent)
    _lane(redrawn, "W_in_1").set("shape", "-75.0,-43.30127 -50.0,0.0 -20.0,0.0 -5.0,0.0")
    profile = _judge(tmp_path, built, redrawn)
    assert _failed(profile) == {} and profile["gate"]["G0"] == "pass"
    shape = next(row for row in profile["invariants"] if row["id"] == "INV-SHAPE")
    assert shape["decision"] == "D3" and list(shape["inherited_bends"]) == ["lane:W_in_1"]
    assert "proposals" not in profile and profile["binding_decisions"]["D3"]["invariant"] == "INV-SHAPE"


def test_g0_rejects_lane_shortening_that_hides_tail_offsets(tmp_path):
    # Review finding: INV-COVERAGE compared coverage within tol_eff only; cutting off a 1.5 m jog whose samples were
    # all beyond tol_eff kept that coverage (0.968) while max lateral fell to 0, so the fact flipped to matched.
    clean = _x4()
    jogged = copy.deepcopy(clean)
    for lane in ("W_in_0", "W_in_1", "W_in_2"):
        (x0, y0), (x1, y1) = _shape(clean, lane)
        y_at = y0 + (y1 - y0) * 3.0 / (x1 - x0)
        _lane(jogged, lane).set("shape", f"{x0},{y0 + 1.5} {x0 + 3},{y_at + 1.5} {x0 + 3},{y_at} {x1},{y1}")
    built = _goal(tmp_path, jogged, reference=clean, mutable_edge_ids=["W_in"])
    assert "lane:W_in_1:centerline" in {row.id for row in built[0].frozen.scored_facts}
    cut = copy.deepcopy(jogged)
    _lane(cut, "W_in_1").set("shape", "-97.0,0.0 -5.0,0.0")
    profile = _judge(tmp_path, built, cut)
    assert profile["facts"]["lane:W_in_1:centerline"][0] == "matched"
    assert _failed(profile)["INV-COVERAGE"] == ["coverage:lane:W_in_1:centerline"]
    measures = invariants.coverage_measures({"reference_coverage": 0.9684, "covered_reference_length_m": 92.0,
                                             "coverage_within_m": {"0.5": 0.9684, "1.0": 0.97}})
    assert measures == {"reference_coverage": 0.9684, "covered_reference_length_m": 92.0,
                        "coverage_within_m:0.5": 0.9684, "coverage_within_m:1.0": 0.97}


def test_g0_rejects_a_net_offset_shift(tmp_path):
    clean = _x4()
    built = _goal(tmp_path, clean)
    candidate = copy.deepcopy(clean)
    candidate.find("location").set("netOffset", "10.00,0.00")
    failed = _failed(_judge(tmp_path, built, candidate))
    assert failed["INV-FRAME"] == ["location:netOffset"]
    assert len(failed["INV-MEASURABLE"]) >= 164
    # Decision D2: a convBoundary that is not the box of the candidate's own geometry is a frame witness.
    moved = copy.deepcopy(clean)
    moved.find("location").set("convBoundary", "-120,-100,100,100")
    profile = _judge(tmp_path, built, moved)
    assert _failed(profile) == {"INV-FRAME": ["location:convBoundary"]} and profile["gate"]["G0"] == "fail"
    frame = next(row for row in profile["invariants"] if row["id"] == "INV-FRAME")
    assert frame["decision"] == "D2" and "deviation 20.000000 m" in frame["reason"]


def test_d2_convboundary_may_change_only_to_the_recomputed_geometry_box(tmp_path):
    clean = _x4()
    built = _goal(tmp_path, clean)
    assert build_profile.geometry_boundary(clean) == [-100.0, -100.0, 100.0, 100.0]
    # netconvert rewrites the same box in its own number format: a byte change equal to the recomputed box passes.
    reformatted = copy.deepcopy(clean)
    reformatted.find("location").set("convBoundary", "-100.000000,-100.000000,100.000000,100.000000")
    profile = _judge(tmp_path, built, reformatted)
    assert _failed(profile) == {} and profile["gate"]["G0"] == "pass"
    # Geometry that reaches further out (an edge shape to x = -110) moves the recomputed box with it.
    extended = copy.deepcopy(clean)
    extended.find("edge[@id='W_in']").set("shape", "-110.00,0.00 0.00,0.00")
    assert build_profile.geometry_boundary(extended) == [-110.0, -100.0, 100.0, 100.0]
    extended.find("location").set("convBoundary", "-110.00,-100.00,100.00,100.00")
    assert "INV-FRAME" not in _failed(_judge(tmp_path, built, extended))
    # Within 0.01 m of the box passes; 0.02 m off, or a box shifted with no geometry behind it, is a witness.
    near = copy.deepcopy(extended)
    near.find("location").set("convBoundary", "-110.005,-100.00,100.00,100.00")
    assert "INV-FRAME" not in _failed(_judge(tmp_path, built, near))
    off = copy.deepcopy(extended)
    off.find("location").set("convBoundary", "-110.02,-100.00,100.00,100.00")
    assert _failed(_judge(tmp_path, built, off))["INV-FRAME"] == ["location:convBoundary"]
    shifted = copy.deepcopy(clean)
    shifted.find("location").set("convBoundary", "-90,-100,110,100")
    assert _failed(_judge(tmp_path, built, shifted))["INV-FRAME"] == ["location:convBoundary"]
    # netOffset, origBoundary and projParameter stay byte-identical: any change, even a reformatting, is a witness.
    for key, value in (("netOffset", "0.0,0.0"), ("origBoundary", "-100,-100,100,101"),
                       ("projParameter", "+proj=utm +zone=32 +ellps=WGS84 +datum=WGS84 +units=m +no_defs")):
        changed = copy.deepcopy(clean)
        changed.find("location").set(key, value)
        assert f"location:{key}" in _failed(_judge(tmp_path, built, changed)).get("INV-FRAME", []), key
    assert invariants.inv_frame([], [{"netOffset": "0,0"}], None)["witness_ids"] == ["location:element",
                                                                                     "location:netOffset"]
    unreadable = invariants.inv_frame([{"convBoundary": "0,0,1,1"}], [{"convBoundary": "0,0,1"}], [0.0, 0.0, 1.0, 1.0])
    assert unreadable["witness_ids"] == ["location:convBoundary"] and "unmeasured" in unreadable["reason"]
    # Attack: a second <location> whose first copy is the parent's shifted netOffset by 1000 m where SUMO looks (it
    # applies the last copy; netconvert and the scorer read the first). Every copy counts, nested ones too.
    doubled = copy.deepcopy(clean)
    second = copy.deepcopy(doubled.find("location"))
    second.set("netOffset", "1000.00,0.00")
    doubled.insert(list(doubled).index(doubled.find("location")) + 1, second)
    assert build_profile.locations(doubled)[0] == build_profile.locations(clean)[0]
    assert _failed(_judge(tmp_path, built, doubled))["INV-FRAME"] == ["location:element", "location:netOffset"]
    copied = copy.deepcopy(clean)
    copied.insert(1, copy.deepcopy(clean.find("location")))
    assert _failed(_judge(tmp_path, built, copied)) == {"INV-FRAME": ["location:element"]}
    nested = copy.deepcopy(clean)
    nested.find("edge[@id='W_in']").append(copy.deepcopy(second))
    assert len(build_profile.locations(nested)) == 2
    assert "location:element" in _failed(_judge(tmp_path, built, nested))["INV-FRAME"]
    # Attack: geometry grown to x = -130 behind a byte-identical convBoundary; an unchanged convBoundary is the box only
    # while the recomputed box stays the parent's.
    stale = copy.deepcopy(clean)
    stale.find("edge[@id='W_in']").set("shape", "-130.00,0.00 -5.00,0.00")
    assert build_profile.geometry_boundary(stale) == [-130.0, -100.0, 100.0, 100.0]
    profile = _judge(tmp_path, built, stale)
    assert _failed(profile) == {"INV-FRAME": ["location:convBoundary"]}
    assert "unchanged -100,-100,100,100; recomputed -130.000000,-100.000000,100.000000,100.000000; deviation " \
           "30.000000 m" in next(row for row in profile["invariants"] if row["id"] == "INV-FRAME")["reason"]


def test_d2_has_no_stale_parent_exception_so_a_wrong_convboundary_cannot_drift_along_a_chain(tmp_path):
    # Review finding (P2): a parent convBoundary already off its geometry box could stay unchanged while the box moved
    # at most 0.01 m from the immediate parent's. Reproduction: convBoundary xmax = 90 over source geometry to
    # xmax = 100, then 50 successive 0.01 m extensions each passed INV-FRAME, ending at xmax = 100.5.
    wrong = [{"convBoundary": "-100,-100,90,100"}]
    for step in range(51):
        box = [-100.0, -100.0, round(100.0 + 0.01 * step, 2), 100.0]
        result = invariants.inv_frame(wrong, wrong, box)
        assert result["witness_ids"] == ["location:convBoundary"], step
        # The same step with convBoundary rewritten to its own box passes.
        right = [{"convBoundary": ",".join(f"{value:.2f}" for value in box)}]
        assert invariants.inv_frame(wrong, right, box)["pass"], step
    # End to end: a goal source whose convBoundary is off its geometry box. An honest speed repair that keeps it is a
    # frame witness; the same repair with convBoundary set to the recomputed box passes INV-FRAME.
    clean = _x4()
    source = copy.deepcopy(clean)
    _lane(source, "W_in_1").set("speed", "10")
    source.find("location").set("convBoundary", "-100,-100,90,100")
    assert build_profile.geometry_boundary(source) == [-100.0, -100.0, 100.0, 100.0]
    built = _goal(tmp_path, source, reference=clean)
    speed = {"id": "op", "operation": "set_speed", "params": {"lane_id": "W_in_1", "speed_m_s": 13.89}}
    kept = copy.deepcopy(source)
    _lane(kept, "W_in_1").set("speed", "13.89")
    profile = _judge(tmp_path, built, kept, operation=speed)
    assert _failed(profile)["INV-FRAME"] == ["location:convBoundary"] and profile["gate"]["G0"] == "fail"
    assert "deviation 10.000000 m" in next(row for row in profile["invariants"] if row["id"] == "INV-FRAME")["reason"]
    fixed = copy.deepcopy(kept)
    fixed.find("location").set("convBoundary", "-100.00,-100.00,100.00,100.00")
    assert "INV-FRAME" not in _failed(_judge(tmp_path, built, fixed, operation=speed))


def _translated(root: ET.Element, dx: float, dy: float) -> ET.Element:
    """A copy of ``root`` with every junction position and every junction, edge and lane shape moved by (dx, dy) and
    convBoundary set to the moved geometry box: the frame shifted while netOffset stays byte-identical."""
    result = copy.deepcopy(root)

    def moved(text: str) -> str:
        return " ".join(f"{float(x) + dx:.6f},{float(y) + dy:.6f}"
                        for x, y in (point.split(",")[:2] for point in text.split()))

    for junction in result.iter("junction"):
        if junction.get("x") is not None:
            junction.set("x", f"{float(junction.get('x')) + dx:.6f}")
            junction.set("y", f"{float(junction.get('y')) + dy:.6f}")
    for element in [*result.iter("junction"), *result.iter("edge"), *result.iter("lane")]:
        if element.get("shape"):
            element.set("shape", moved(element.get("shape")))
    result.find("location").set("convBoundary",
                                ",".join(f"{value:.6f}" for value in build_profile.geometry_boundary(result)))
    return result


def test_d2_a_translated_frame_is_a_frame_change(tmp_path):
    # Attack: every coordinate moved by (0.04, 0.04) m, netOffset kept and convBoundary set to the moved box passed
    # INV-FRAME and INV-SHAPE, and 0.04 m stays below V's materiality, so a speed fix carried the frame shift into an
    # improved candidate. Moving all geometry under a fixed netOffset is the frame change netOffset identity forbids.
    clean = _x4()
    source = copy.deepcopy(clean)
    _lane(source, "W_in_1").set("speed", "10")
    built = _goal(tmp_path, source, reference=clean)
    speed = {"id": "op", "operation": "set_speed", "params": {"lane_id": "W_in_1", "speed_m_s": 13.89}}
    for offset in (0.04, 0.3, 2.0):
        shifted = _translated(source, offset, offset)
        _lane(shifted, "W_in_1").set("speed", "13.89")
        profile = _judge(tmp_path, built, shifted, operation=speed, seeds=offset == 0.04, l2=offset == 0.04)
        # W and J0 are the step's mutable junctions; the three outside them anchor the frame, and none stayed.
        assert _failed(profile)["INV-FRAME"] == ["anchor:junction:E", "anchor:junction:N", "anchor:junction:S",
                                                 "anchor:none_fixed"]
        assert profile["gate"]["G0"] == "fail" and profile["gate"]["screening"] == "g0_rejected"
    # verify-edit's derived scope (the plan) cannot widen the anchor: only the goal's own scope may move.
    widened = _judge(tmp_path, built, _translated(source, 0.04, 0.04), operation=speed,
                     plan={"mutable_junction_ids": ["E", "J0", "N", "S", "W"]})
    assert _failed(widened)["INV-FRAME"][:3] == ["anchor:junction:E", "anchor:junction:N", "anchor:junction:S"]
    # The honest repair keeps every position and passes.
    repaired = copy.deepcopy(source)
    _lane(repaired, "W_in_1").set("speed", "13.89")
    assert _failed(_judge(tmp_path, built, repaired, operation=speed)) == {}
    positions = build_profile.junction_positions(source)
    assert sorted(positions) == ["E", "J0", "N", "S", "W"]
    shifted = build_profile.junction_positions(_translated(source, 0.04, 0.04))
    # A scope that holds every junction still needs one junction to keep its position.
    every = invariants.inv_frame([], [], None, anchor={"parent": positions, "candidate": shifted,
                                                       "scope": sorted(positions)})
    assert every["witness_ids"] == ["anchor:none_fixed"]
    # A junction inside the scope may move; netconvert's 0.01 m rewrite of a position (at most 0.005 m) is no shift.
    local = {**positions, "W": (-101.0, 0.0), "E": (100.004, 0.0)}
    assert invariants.inv_frame([], [], None, anchor={"parent": positions, "candidate": local,
                                                      "scope": ["J0", "W"]})["pass"]
    far = {**positions, "E": (100.006, 0.0)}
    assert invariants.inv_frame([], [], None, anchor={"parent": positions, "candidate": far,
                                                      "scope": ["J0", "W"]})["witness_ids"] == ["anchor:junction:E"]
    # The epsilon does not accumulate along a chain: a junction the chain kept at the goal source's position keeps it.
    step1 = {**positions, "E": (100.004, 0.0)}
    step2 = {**positions, "E": (100.008, 0.0)}
    drift = invariants.frame_anchor(step1, step2, ["J0", "W"], source=positions)
    assert drift[0] == ["anchor:junction:E"] and "1 drifted from the goal source's" in drift[1]
    # One an earlier step moved inside its own scope only keeps its parent position.
    joined = {**positions, "E": (101.0, 0.0)}
    assert invariants.frame_anchor(joined, {**joined, "E": (101.004, 0.0)}, ["J0", "W"], source=positions)[0] == []


def test_g0_rejects_a_coordinate_change_on_an_e1_lane(tmp_path):
    clean = _x4()
    detectors = tmp_path / "e1.add.xml"
    detectors.write_bytes(b'<additional><inductionLoop id="z1" lane="W_in_0" pos="50" period="900" file="x.xml"/>'
                          b'</additional>')
    built = _goal(tmp_path, clean, mutable_edge_ids=["W_in"], lane_artifacts=[detectors])
    candidate = copy.deepcopy(clean)
    # A new interior vertex 1.6 cm off the line: the lane end, its internal path and every fact stay put.
    _lane(candidate, "W_in_0").set("shape", "-100.0,0.0 -50.0,-1.7 -5.0,-3.2")
    profile = _judge(tmp_path, built, candidate, artifacts=[detectors], affected=("W_in_0",))
    failed = _failed(profile)
    assert failed == {"INV-E1-LANE": ["e1:z1@W_in_0"]}
    unchecked = _judge(tmp_path, built, candidate)
    assert set(_failed(unchecked)) == {"INV-ARTIFACTS", "INV-E1-LANE"}


def test_g0_rejects_out_of_scope_ops_and_new_direction_findings(tmp_path):
    clean = _x4()
    built = _goal(tmp_path, clean, ops=["move_stop_line"])
    wrong = apply_synthetic_mutation(copy.deepcopy(clean), "wrong-turn")
    profile = _judge(tmp_path, built, wrong, operation={"id": "op", "operation": "add_lane_connection",
                                                        "params": {"from": "N_in", "fromLane": 0, "to": "W_out",
                                                                   "toLane": 0}})
    failed = _failed(profile)
    assert failed["INV-OPSCOPE"] == ["op:add_lane_connection"]
    assert failed["INV-DIR"] == ["dir:W_in_0>S_out_0"]
    assert failed["INV-AUDIT"] == ["audit:J0:connection_mode:turn_lane_order_inversion:W_in:s>l"]


def test_g0_rejects_a_new_wrong_label_on_a_connection_the_parent_already_flags(tmp_path):
    # Review finding: the direction identity was the connection alone, so relabelling W_in_0>S_out_0 from l to s on
    # the wrong-turn parent passed INV-DIR.
    wrong = apply_synthetic_mutation(_x4(), "wrong-turn")
    built = _goal(tmp_path, wrong, reference=_x4())
    relabelled = copy.deepcopy(wrong)
    next(row for row in relabelled.findall("connection") if row.get("from") == "W_in" and row.get("fromLane") == "0"
         and row.get("to") == "S_out").set("dir", "s")
    assert _failed(_judge(tmp_path, built, relabelled))["INV-DIR"] == ["dir:W_in_0>S_out_0"]
    assert "INV-DIR" not in _failed(_judge(tmp_path, built, copy.deepcopy(wrong)))


def _without_w_in_0(root: ET.Element, label: str) -> ET.Element:
    """``root`` with W_in_0 removed and W_in_1, W_in_2 renumbered to W_in_0, W_in_1 (``remove_index`` 0); the right
    turn to S_out_0 (its internal lane starting at the survivor's end) moves onto the survivor now at index 0,
    labelled ``label``."""
    result = copy.deepcopy(root)
    edge = result.find("edge[@id='W_in']")
    edge.remove(_lane(result, "W_in_0"))
    for lane in edge.findall("lane"):
        index = int(lane.get("index")) - 1
        lane.set("index", str(index))
        lane.set("id", f"W_in_{index}")
    turn = _lane(result, ":J0_0_0")
    turn.set("shape", " ".join([_lane(result, "W_in_0").get("shape").split()[-1], *turn.get("shape").split()[1:]]))
    junction = result.find("junction[@id='J0']")
    junction.set("incLanes", junction.get("incLanes").replace("W_in_0 W_in_1 W_in_2", "W_in_0 W_in_1"))
    for row in result.findall("connection"):
        if row.get("from") == "W_in" and row.get("fromLane") == "0":
            row.set("dir", label)
        elif row.get("from") == "W_in":
            row.set("fromLane", str(int(row.get("fromLane")) - 1))
    return result


def test_g0_rejects_a_new_wrong_label_on_a_survivor_renumbered_into_a_removed_lane_index(tmp_path):
    # Review finding: INV-DIR kept a baseline finding on a removed lane under its old index, so removing W_in_0
    # (whose right turn the wrong-turn source labels l) and renumbering the clean W_in_1 into index 0 let the same
    # wrong label on that survivor match the removed lane's finding and pass.
    wrong = apply_synthetic_mutation(_x4(), "wrong-turn")
    table = _facts_for_kinds(tmp_path, _x4(), ("lane_count",), ("W_in",))
    next(fact for fact in table["facts"] if fact["kind"] == "lane_count")["expected"] = 1
    built = _goal(tmp_path, wrong, reference=_x4(), facts=table)
    remove = {"id": "op", "operation": "set_lane_count", "params": {"edge_id": "W_in", "num_lanes": 2,
                                                                    "remove_index": 0}}
    plan = {"lane_index_mapping": {"1": 0, "2": 1}}
    # The removed lane takes its finding with it: the survivor labelled r is clean and passes G0.
    correct = _judge(tmp_path, built, _without_w_in_0(wrong, "r"), operation=remove, plan=plan)
    assert _failed(correct) == {} and correct["gate"]["G0"] == "pass", correct["gate"]["first_fail"]
    # The survivor labelled l carries a new finding under the removed lane's former identity.
    relabelled = _judge(tmp_path, built, _without_w_in_0(wrong, "l"), operation=remove, plan=plan)
    assert _failed(relabelled) == {"INV-DIR": ["dir:W_in_0>S_out_0"]}
    assert (relabelled["gate"]["G0"], relabelled["gate"]["screening"]) == ("fail", "g0_rejected")
    assert [row["id"] for row in relabelled["gate"]["new_direction_findings"]] == ["dir:W_in_0>S_out_0"]


def test_structural_failures_carry_measured_gaps_that_inv_materialize_compares(tmp_path):
    # Review finding: a pre-existing endpoint gap that grew was invisible to INV-MATERIALIZE; the identity now keeps
    # its measured length beside it.
    def gapped(offset: float) -> ET.Element:
        root = _x4()
        lane = _lane(root, ":J0_0_0")
        points = _shape(root, ":J0_0_0")
        points[-1] = [points[-1][0], points[-1][1] - offset]
        lane.set("shape", " ".join(f"{x},{y}" for x, y in points))
        return root

    small, large = (build_profile.structural_failures(gapped(offset)) for offset in (0.5, 2.0))
    assert [identity for identity, _ in small] == [identity for identity, _ in large] and small
    assert all(measure is not None for _, measure in small + large)
    assert max(m for _, m in large) > max(m for _, m in small) + invariants.MEASURE_EPSILON_M
    built = _goal(tmp_path, gapped(0.5), reference=gapped(0.5))
    profile = _judge(tmp_path, built, gapped(2.0), structural=len(large))
    materialize = next(row for row in profile["invariants"] if row["id"] == "INV-MATERIALIZE")
    # Decision D1 on a dirty source: the inherited gap may stay, but it may not grow.
    assert not materialize["pass"] and materialize["source_dirty"] and profile["source_dirty"]
    assert materialize["witness_ids"] and all(witness.startswith("worse:") for witness in materialize["witness_ids"])


# ---- L3 hard rule (synthetic, acceptance (e)) ----------------------------------------------------------------------

def _audit(failing=(), *, collisions=0, teleports=0, routes="pass", removed=(), status=None, kind=None) -> dict:
    """A routeability audit in the real report shape: status and routeability_status follow its events."""
    kind = kind or ("collision-failure" if collisions else "teleport-failure" if teleports
                    else "vehicle-route-completion-failed" if failing else "unverified_endpoints" if removed
                    else "pass")
    status = status or ("pass" if kind == "pass" else "review_required" if kind == "unverified_endpoints" else "fail")
    return {"status": status, "routeability_status": kind, "route_generation": {"status": routes},
            "cohort_coverage": {"removed_endpoint_vehicle_ids": list(removed)},
            "final_attempt": {"vehicle_routes": {"failures": [{"vehicle_id": v, "reason": "vehicle_did_not_arrive_in_"
                                                                                         "window"} for v in failing],
                                                 "missing_vehicle_ids": []},
                              "inspection": {"summary": {"collisions": collisions, "teleports": teleports}}}}


SEEDS = [17, 42, 104, 7, 23, 37, 59, 71, 83, 97]


def test_l3_rejects_four_worse_seeds_with_one_new_collision_and_six_ties():
    worse = {17: _audit(collisions=1), 42: _audit(["v1"]), 104: _audit(["v2"]), 7: _audit(["v3", "v4"])}
    pairs = [(seed, _audit(), worse.get(seed, _audit())) for seed in SEEDS]
    result = build_gate.l3_rule(pairs, SEEDS)
    assert (result["status"], result["result"]) == ("blocked", "new_failing_requests")
    assert [row["seed"] for row in result["seeds"] if row["result"] == "new_failing_requests"] == [17, 42, 104, 7]
    assert sum(row["result"] == "subset_holds" for row in result["seeds"]) == 6


def test_l3_rejects_lost_arrivals_and_a_fix_that_breaks_another_request():
    lost = build_gate.l3_rule([(42, _audit(), _audit(["a", "b"]))], [42])
    assert lost["result"] == "new_failing_requests" and lost["seeds"][0]["new_failing"] == ["a", "b"]
    traded = build_gate.l3_rule([(42, _audit(["a"]), _audit(["b"]))], [42])
    assert traded["result"] == "new_failing_requests" and traded["seeds"][0]["new_failing"] == ["b"]
    unroutable = build_gate.l3_rule([(42, _audit(), _audit(removed=["c"]))], [42])
    assert unroutable["result"] == "new_failing_requests"
    assert build_gate.screening({"pass": True}, {"verdict": "improved"}, traded, {"status": "pass"})[0] == \
        "dynamic_hard"


def test_l3_dirty_sources_pass_only_identified_subsets_and_never_unidentified_events():
    subset = build_gate.l3_rule([(42, _audit(["a", "b"]), _audit(["a"]))], [42])
    assert (subset["status"], subset["source_state"]) == ("pass", "dirty on seeds [42]: not promotable without a "
                                                                   "human")
    collided = build_gate.l3_rule([(42, _audit(collisions=1), _audit(collisions=1))], [42])
    assert collided["result"] == "identities_unavailable" and collided["status"] == "blocked"
    assert build_gate.IDENTITY_REASON in collided["seeds"][0]["reason"]
    hidden = build_gate.l3_rule([(42, _audit(teleports=2), _audit(["x"]))], [42])
    assert hidden["result"] == "identities_unavailable"
    assert build_gate.screening({"pass": True}, {"verdict": "improved"}, hidden, {"status": "pass"})[0] == \
        "dynamic_unverified"
    assert build_gate.l3_rule([], [42])["result"] == "not_run"
    assert build_gate.l3_rule([(42, _audit(), _audit())], [42, 17])["result"] == "seeds_differ"
    assert build_gate.l3_rule([(42, _audit(), _audit(routes="fail"))], [42])["result"] == "new_failing_requests"


def test_l3_never_passes_a_failed_audit_its_request_rows_do_not_explain():
    # Review finding: _failing ignored the audit's own status, so an output-mismatch or input-artifact-changed
    # candidate with no failure rows passed, and so did a seed where both route generations failed.
    for kind in ("output-mismatch", "input-artifact-changed", "invalid-output", "sumo-run-failed"):
        seed = build_gate.l3_seed(42, _audit(), _audit(status="fail", kind=kind))
        assert (seed["status"], seed["result"]) == ("blocked", "identities_unavailable"), kind
        assert kind in seed["reason"]
    unnamed = build_gate.l3_seed(42, _audit(), _audit(status="fail", kind="incomplete"))
    assert unnamed["result"] == "identities_unavailable" and "names no failing request" in unnamed["reason"]
    both = build_gate.l3_seed(42, _audit(routes="fail"), _audit(routes="fail"))
    assert (both["status"], both["result"]) == ("blocked", "identities_unavailable")
    assert build_gate.l3_seed(42, _audit(["a"]), _audit(["a"]))["status"] == "pass"


def test_l3_requires_the_goal_frozen_trips_on_every_seed():
    # Review finding: verify-edit regenerates trips from the ledger source, so a chained step could run other
    # requests than the goal source's; L3 now compares both audits' trip identities with the goal record.
    def on(sha: str, audit: dict, key: str) -> dict:
        return {**audit, "od_policy": {key: sha}}

    goal_sha, other = "a" * 64, "b" * 64
    same = [(42, on(goal_sha, _audit(), "request_file_sha256"), on(goal_sha, _audit(), "frozen_trip_sha256"))]
    assert build_gate.l3_rule(same, [42], {42: goal_sha})["result"] == "subset_holds"
    drifted = [(42, on(other, _audit(), "request_file_sha256"), on(other, _audit(), "frozen_trip_sha256"))]
    result = build_gate.l3_rule(drifted, [42], {42: goal_sha})
    assert result["result"] == "identities_unavailable" and "frozen trips" in result["reason"]
    assert build_gate.l3_rule(same, [42], {42: None})["result"] == "identities_unavailable"
    assert build_gate.l3_rule(drifted, [42])["result"] == "subset_holds"


def test_screening_never_accepts_an_unrun_or_failed_l2():
    # Review finding (P1-b): L2 never ran, yet improved_l2_not_run was an accepted parent state, and any non-pass L2
    # mapped to that accepted label.
    passing = build_gate.l3_rule([(42, _audit(), _audit())], [42])
    improved, g0 = {"verdict": "improved"}, {"pass": True}
    assert build_gate.ACCEPTED_SCREENINGS == ("improved",)
    assert build_gate.screening(g0, improved, passing) == ("l2_not_run", build_gate.L2_NOT_RUN)
    assert build_gate.screening(g0, improved, passing, {"status": "pass"}) == ("improved", None)
    for result, label in (("new_failures", "l2_new_failures"), ("unverified", "l2_unverified"),
                          ("not_run", "l2_not_run")):
        assert build_gate.screening(g0, improved, passing, {"status": "blocked", "result": result,
                                                            "reason": "x"}) == (label, "x")
        assert label not in build_gate.ACCEPTED_SCREENINGS
    # L2 comes before L3 (the design's tier order); a failing L3 still blocks after a passing L2.
    hard = build_gate.l3_rule([(42, _audit(), _audit(["v"]))], [42])
    assert build_gate.screening(g0, improved, hard, {"status": "pass"})[0] == "dynamic_hard"


# ---- review round 2 regressions (P1-a lane remap chain, P1-b L2, P1-c Hausdorff, P1-d G0, frozen trips, sg) -----

def _add_right_lane(root: ET.Element, edge_id: str, offset: float = 3.2) -> ET.Element:
    """``root`` with one more lane on the right of ``edge_id`` (new index 0, every old lane one index up), as a
    lane-count step lays it out ``offset`` metres right of the old lane 0; the old lanes keep their shapes and their
    connections follow them. The new lane is a bicycle lane, so the connection audit does not ask for a motor
    connection onto it."""
    result = copy.deepcopy(root)
    edge = result.find(f"edge[@id='{edge_id}']")
    lanes = edge.findall("lane")
    for lane in reversed(lanes):
        index = int(lane.get("index")) + 1
        lane.set("index", str(index))
        lane.set("id", f"{edge_id}_{index}")
    added = copy.deepcopy(lanes[0])
    points = [[float(value) for value in token.split(",")] for token in lanes[0].get("shape").split()]
    added.set("index", "0")
    added.set("id", f"{edge_id}_0")
    added.set("allow", "bicycle")
    added.set("shape", " ".join(f"{x},{y - offset}" for x, y in points))
    edge.insert(list(edge).index(lanes[0]), added)
    for row in result.findall("connection"):
        for side in ("from", "to"):
            if row.get(side) == edge_id:
                row.set(f"{side}Lane", str(int(row.get(f"{side}Lane")) + 1))
    return result


def test_lane_count_remaps_compose_along_the_parent_chain(tmp_path):
    # Review finding (P1-a): after a set_lane_count step the next step reset the candidate table to the goal's and
    # rescored the parent with it, so renaming E_out_1 to E_out_2 turned a matched width fact into a 0.6 m mismatch
    # on E_out_1, and editing that wrong physical lane scored as improved.
    source = _x4()
    _lane(source, "E_out_0").set("width", "3.0")
    _lane(source, "E_out_1").set("width", "3.6")
    table = _facts_for_kinds(tmp_path, source, ("lane_width", "lane_count"), ("E_out",))
    # The reference expects one bicycle lane on E_out, which the source lacks.
    next(fact for fact in table["facts"] if fact["kind"] == "lane_count")["expected"] = 1
    built = _goal(tmp_path, source, facts=table, mutable_edge_ids=["E_out"], mutable_junction_ids=["J0"])
    root_profile = _written(built, tmp_path / "source")
    add = {"id": "op", "operation": "set_lane_count", "params": {"edge_id": "E_out", "num_lanes": 4,
                                                                 "add_side": "right"}}
    plan = {"mutable_junction_ids": ["E", "J0"], "mutable_edge_ids": ["E_out"],
            "lane_index_mapping": {"0": 1, "1": 2, "2": 3}}
    first = _add_right_lane(source, "E_out")
    step1 = _judge(tmp_path, built, first, operation=add, plan=plan, seeds=True, l2=True, parent_profile=root_profile,
                   out=tmp_path / "s1")
    assert _failed(step1) == {} and step1["gate"]["screening"] == "improved", step1["gate"]["first_fail"]
    assert step1["V"]["parent"] == [1, 0, 0, 0, 0.0] and step1["V"]["cand"] == [0, 0, 0, 0, 0.0]
    effective = step1["inputs"]["effective_facts"]
    assert (effective["basis"], effective["verified"], effective["remaps"]) == ("declared_remap", True, 1)
    # Scored with the goal's selectors instead, E_out_1 is physically the old E_out_0: the reviewer's 0.6 m mismatch.
    loaded, _, facts_bytes, _, _ = built
    stale = build_profile.score(_write(first, tmp_path / "first.net.xml").read_bytes(),
                                build_profile.parse_table(facts_bytes))
    width = next(row for row in stale["facts"] if row["id"] == "lane:E_out_1:width")
    assert width["status"] == "mismatched" and width["metrics"]["absolute_width_error_m"] == pytest.approx(0.6)
    # Step 2 "repairs" E_out_1 as that stale score suggests: it edits the wrong physical lane and must not improve.
    wrong = copy.deepcopy(first)
    _lane(wrong, "E_out_1").set("width", "3.6")
    edit = {"id": "op", "operation": "set_lane_width", "params": {"lane_id": "E_out_1", "width_m": 3.6}}
    step2 = _judge(tmp_path, built, wrong, operation=edit, plan={"mutable_edge_ids": ["E_out"]}, seeds=True, l2=True,
                   parent_net=Path(step1["inputs"]["net"]["path"]), parent_profile=tmp_path / "s1" / "profile.full.json",
                   out=tmp_path / "s2")
    assert step2["V"]["parent"] == step1["V"]["cand"] == [0, 0, 0, 0, 0.0]
    assert step2["gate"]["regressed_facts"] == ["lane:E_out_0:width"] and step2["gate"]["screening"] == "g0_rejected"
    assert step2["facts"]["lane:E_out_1:width"] == ["matched", 0.0]
    assert step2["inputs"]["effective_facts"]["basis"] == "inherited"
    assert step2["inputs"]["effective_facts"]["sha256"] == effective["sha256"]
    # A second lane-count step composes with the first: lane:E_out_1:width now selects E_out_3.
    again = _add_right_lane(first, "E_out")
    plan["lane_index_mapping"] = {"0": 1, "1": 2, "2": 3, "3": 4}
    step3 = _judge(tmp_path, built, again, operation={**add, "params": {**add["params"], "num_lanes": 5}}, plan=plan,
                   seeds=True, l2=True, parent_net=Path(step1["inputs"]["net"]["path"]),
                   parent_profile=tmp_path / "s1" / "profile.full.json", out=tmp_path / "s3")
    composed = step3["inputs"]["effective_facts"]
    assert (composed["remaps"], composed["verified"]) == (2, True)
    selectors = {fact["id"]: fact["selector"] for fact in json.loads(Path(composed["path"]).read_text())["facts"]}
    assert selectors["lane:E_out_1:width"] == {"lane_id": "E_out_3"}
    assert step3["facts"]["lane:E_out_1:width"][0] == "matched"
    assert step3["facts"]["edges:E_out:lane_count:bicycle"][0] == "mismatched"
    # A changed effective table breaks the chain like a changed net.
    Path(effective["path"]).write_bytes(Path(effective["path"]).read_bytes() + b"\n")
    with pytest.raises(ValueError, match="effective fact table of parent"):
        _judge(tmp_path, built, wrong, operation=edit, parent_net=Path(step1["inputs"]["net"]["path"]),
               parent_profile=tmp_path / "s1" / "profile.full.json")


def _facts_for_kinds(tmp_path: Path, root: ET.Element, kinds: tuple[str, ...], edges: tuple[str, ...]) -> dict:
    net = _write(root, tmp_path / "kinds-reference.net.xml")
    parameters = ReferenceFactParameters(kinds=kinds, edge_ids=edges, lane_count_vehicle_class="bicycle")
    return build_reference_net_facts(net, site_id="x4", target_date="2026-10-25", parameters=parameters)[0]


def test_l2_new_failures_are_differential_after_the_lane_remap_and_bound_to_the_candidate():
    # Review finding (P1-b): L2 never ran. The rule compares failure tokens of both nets per touched junction.
    run = {"status": "ran", "run_ok": True, "driven": [["a_0", "b_0"], ["a_1", "b_0"]], "arrived": [["a_0", "b_0"]],
           "skipped": [], "events": {"teleports": 0, "collisions": 1}}
    assert build_gate.l2_failing(run, [["a_0", "b_0"], ["c_0", "b_0"]]) == {
        "movement:a_1>b_0", "movement:c_0>b_0", "run:collisions"}
    assert build_gate.l2_failing({**run, "run_ok": False}, []) == {"run:failed", "movement:a_0>b_0",
                                                                   "movement:a_1>b_0", "run:collisions"}
    assert build_gate.l2_failing({"status": "absent"}, [["a_0", "b_0"]]) == {"junction:absent", "movement:a_0>b_0"}
    ok = {"status": "ran", "run_ok": True, "driven": [], "arrived": [], "skipped": [], "events": {}}

    def record(parent_run, candidate_run, **extra):
        return {"schema": build_gate.MOVEMENT_GUARD_SCHEMA, "method": "all_turn_smoke", "parent": {"sha256": "p"},
                "candidate": {"sha256": "c"}, "verify_edit": {"sha256": "v"}, "junctions": ["J"],
                "expected": {"parent": {}, "candidate": {}}, "lane_mapping": {"e": {"0": "1", "1": "2"}},
                "runs": {"parent": {"J": parent_run}, "candidate": {"J": candidate_run}}, "nets_unchanged": True,
                **extra}

    bound = {"parent_sha256": "p", "candidate_sha256": "c", "junctions": ["J"],
             "expected": {"parent": {}, "candidate": {}}, "lane_mapping": {"e": {"0": "1", "1": "2"}},
             "verify_edit_sha256": "v"}
    # The parent's e_0 failure is the candidate's e_1 failure after the remap: not new.
    parent = {**ok, "driven": [["e_0", "x_0"]]}
    same = {**ok, "driven": [["e_1", "x_0"]]}
    assert build_gate.l2_rule(record(parent, same), **bound)["status"] == "pass"
    fresh = build_gate.l2_rule(record(parent, {**ok, "driven": [["e_2", "x_0"]]}), **bound)
    assert (fresh["result"], fresh["new_failures"]) == ("new_failures", ["J:movement:e_2>x_0"])
    unbound = build_gate.l2_rule(record(ok, ok), **{**bound, "candidate_sha256": "other"})
    assert (unbound["status"], unbound["result"]) == ("blocked", "unverified")
    assert build_gate.l2_rule(record(ok, ok, nets_unchanged=False), **bound)["result"] == "unverified"
    assert build_gate.l2_rule(record(ok, ok), **{**bound, "verify_edit_sha256": "w"})["result"] == "unverified"
    assert build_gate.l2_rule(None, **bound)["result"] == "not_run"
    # A parent whose smoke did not run cleanly leaves nothing to compare: never a pass.
    assert build_gate.l2_rule(record({**ok, "run_ok": False}, ok), **bound)["result"] == "unverified"
    # A junction a join merged away expects no movement there: its absence alone is no failure.
    assert build_gate.l2_failing({"status": "absent"}, []) == set()


def test_l2_runs_on_every_touched_junction_and_a_broken_movement_blocks_the_candidate(tmp_path):
    clean = _x4()
    faulted = copy.deepcopy(clean)
    _lane(faulted, "W_in_1").set("speed", "10")
    built = _goal(tmp_path, faulted, reference=clean)
    speed = {"id": "op", "operation": "set_speed", "params": {"lane_id": "W_in_1", "speed_m_s": 13.89}}
    repaired = copy.deepcopy(faulted)
    _lane(repaired, "W_in_1").set("speed", "13.89")
    good = _judge(tmp_path, built, repaired, operation=speed, seeds=True, l2=True)
    assert good["gate"]["screening"] == "improved"
    rows = good["gate"]["L2"]["junctions"]
    assert [row["junction"] for row in rows] == ["J0", "W"] and all(not row["new"] for row in rows)
    guard = json.loads(Path(good["inputs"]["movement_guard"]["path"]).read_text(encoding="utf-8"))
    # Expected movements are the frozen movement facts at each touched junction: 12 at J0, none at the fringe W.
    assert sorted(guard["expected"]["candidate"]) == ["J0"] and len(guard["expected"]["candidate"]["J0"]) == 12
    broken = _judge(tmp_path, built, repaired, operation=speed, seeds=True, l2=True,
                    broken=(("W_in_1", "E_out_1"),))
    assert broken["gate"]["screening"] == "l2_new_failures" and broken["status"] == "measured"
    assert broken["gate"]["L2"]["new_failures"] == ["J0:movement:W_in_1>E_out_1"]


def test_l2_uses_the_movement_probes_when_both_nets_have_a_supported_manifest(tmp_path, monkeypatch):
    # The design's L2 branch: run_candidate_movement_probes on supported manifests, else the all-turn smoke.
    from astra_sumo.core import movement_routeability

    nets = {role: _write(_x4(), tmp_path / f"{role}.net.xml") for role in ("parent", "candidate")}
    _lane(root := _x4(), "W_in_1").set("speed", "10")
    nets["candidate"] = _write(root, tmp_path / "candidate.net.xml")
    manifests = {}
    for role, net in nets.items():
        manifests[role] = tmp_path / f"{role}.manifest.json"
        manifests[role].write_text(json.dumps({"schema": movement_guard.MANIFEST_SCHEMAS[0], "artifacts": {
            "network": {"path": str(net), "sha256": file_sha(net)}}}), encoding="utf-8")

    def probes(*, candidate_manifest, output_dir, sumo_binary):
        output_dir.mkdir(parents=True)
        report = output_dir / "summary.json"
        report.write_text("{}", encoding="utf-8")
        broken = "candidate" in Path(candidate_manifest).name
        return {"source_immutable": True, "probe_count": 2, "not_tested": [], "report_file": str(report),
                "results": [{"sumo_connection": ["W_in", 1, "E_out", 1], "status": "review_required" if broken
                             else "pass"}, {"sumo_connection": ["W_in", 0, "S_out", 0], "status": "pass"}]}

    monkeypatch.setattr(movement_routeability, "run_candidate_movement_probes", probes)
    expected = {"parent": {}, "candidate": {}}
    record = movement_guard.run_movement_guard(parent_net=nets["parent"], candidate_net=nets["candidate"],
                                               junctions=["J0"], expected=expected, lane_mapping={},
                                               output_dir=tmp_path / "guard", sumo_binary="sumo",
                                               verify_edit={"sha256": "v"}, manifests=manifests)
    assert record["method"] == "movement_probes" and list(record["runs"]["candidate"]) == [build_gate.NETWORK_RUN]
    result = build_gate.l2_rule(record, parent_sha256=file_sha(nets["parent"]),
                                candidate_sha256=file_sha(nets["candidate"]), junctions=["J0"], expected=expected)
    assert result["new_failures"] == ["*:movement:W_in_1>E_out_1"] and result["result"] == "new_failures"
    # A manifest bound to another net is unsupported: the smoke runs instead.
    assert not movement_guard.supported_manifest(manifests["parent"], file_sha(nets["candidate"]))


def test_inv_shape_counts_endpoint_projections_in_the_directed_hausdorff_distance():
    # Review finding (P1-c): samples whose nearest parent point was an endpoint were dropped, so an extension
    # beyond the parent end (200, 50) measured 0 m and passed; the true distance is 111.803 m.
    parent, candidate = [(0.0, 0.0), (100.0, 0.0)], [(0.0, 0.0), (100.0, 0.0), (200.0, 50.0)]
    assert invariants.directed_hausdorff_m(candidate, parent) == pytest.approx(111.80339887)
    change = invariants.shape_change(parent, candidate)
    result = invariants.inv_shape({"lane:x_0": change}, dict(goal.DEFAULT_G0_SHAPE))
    assert not result["pass"] and "hausdorff:lane:x_0" in result["witness_ids"]
    # A lane added on an edge the parent lacks has no reference: only its own turns count.
    added = invariants.shape_change(None, [(0.0, 0.0), (50.0, 0.0), (100.0, 0.0)])
    assert added["directed_hausdorff_m"] is None and added["vertex_delta"] is None
    assert invariants.inv_shape({"lane:x_1": added}, dict(goal.DEFAULT_G0_SHAPE))["pass"]


BEND = [(-75.0, -43.30127), (-50.0, 0.0), (-5.0, 0.0)]


def test_d3_only_an_unchanged_bend_is_inherited():
    shape = dict(goal.DEFAULT_G0_SHAPE)

    def judged(candidate, parent=BEND):
        return invariants.inv_shape({"lane:x_1": invariants.shape_change(parent, candidate)}, shape)

    # The parent's 60 deg bend, unchanged (one collinear vertex added after it): inherited, exempt.
    same = judged([(-75.0, -43.30127), (-50.0, 0.0), (-20.0, 0.0), (-5.0, 0.0)])
    assert same["pass"] and same["inherited_bends"] == {"lane:x_1": [[-50.0, 0.0, pytest.approx(-60.0, abs=1e-4)]]}
    # Within 0.01 m of the parent vertex and 0.5 deg of its turn it is still the parent's bend.
    assert judged([(-75.0, -43.30127), (-50.005, 0.0), (-5.0, 0.0)])["pass"]
    # A new 40 deg turn next to the inherited bend is bounded strictly.
    turned = judged([(-75.0, -43.30127), (-50.0, 0.0), (-7.0, 0.0), (-5.0, 2 * math.tan(math.radians(40)))])
    assert turned["witness_ids"] == ["heading:lane:x_1"] and list(turned["inherited_bends"]) == ["lane:x_1"]
    # The bend vertex moved 0.05 m is no longer the parent's: its 60 deg turn is bounded.
    moved = judged([(-75.0, -43.30127), (-50.05, 0.0), (-5.0, 0.0)])
    assert moved["witness_ids"] == ["heading:lane:x_1"] and moved["inherited_bends"] == {}
    # The same vertex turning 2.9 deg more sharply (its next vertex moved) is not inherited either.
    assert judged([(-75.0, -43.30127), (-50.0, 0.0), (-30.0, -1.0), (-5.0, 0.0)])["witness_ids"] == [
        "heading:lane:x_1"]
    # A lane without a parent counterpart inherits no bend.
    added = invariants.shape_change(None, [(0.0, 0.0), (10.0, 0.0), (15.0, 8.660254)])
    assert invariants.inv_shape({"lane:x_2": added}, shape)["witness_ids"] == ["heading:lane:x_2"]
    # Attack: the same vertex turning by the same angle the other way (signed -60 -> +60 deg; its 2.5 m first segment
    # stays inside the 3 m Hausdorff bound) matched the unsigned turn and passed as the parent's bend.
    vertex, length = (-98.0, 0.0), 2.5
    start = (vertex[0] - length * math.cos(math.radians(60)), -length * math.sin(math.radians(60)))
    parent = [start, vertex, (-5.0, 0.0)]
    assert invariants._turns(parent)[0][2] == pytest.approx(-60.0)
    mirrored = judged([(start[0], -start[1]), vertex, (-5.0, 0.0)], parent)
    assert mirrored["witness_ids"] == ["heading:lane:x_1"] and mirrored["inherited_bends"] == {}
    assert invariants.turn_difference_deg(179.9, -179.9) == pytest.approx(0.2)


def _sharpened(extra_deg: float, base=BEND, length: float = 1.0) -> list[tuple[float, float]]:
    """``base`` (a clockwise bend at its middle vertex) with a vertex ``length`` m after the bend that turns the lane
    ``extra_deg`` further clockwise there: the bend vertex stays, its turn grows by ``extra_deg``."""
    (ax, ay), (vx, vy), end = base
    heading = math.radians(-extra_deg)
    return [(ax, ay), (vx, vy), (vx + length * math.cos(heading), vy + length * math.sin(heading)), end]


def test_d3_an_inherited_bend_is_anchored_to_the_goal_source():
    # Attack: the exemption matched the immediate parent only, so a chain kept the bend vertex and sharpened its turn
    # 0.45 deg per step, every step passing against its parent (60 -> 65.4 deg over 12 steps; up to about 32 deg over
    # MAX_PARENT_CHAIN steps). D3 is now anchored to the goal source as D1 is.
    shape = dict(goal.DEFAULT_G0_SHAPE)
    steps = [BEND] + [_sharpened(0.45 * k) for k in range(1, 13)]
    turns = [invariants._turns(points)[0][2] for points in steps]
    assert turns[0] == pytest.approx(-60.0) and turns[-1] == pytest.approx(-65.4)

    def judged(parent, candidate, source=BEND):
        return invariants.inv_shape({"lane:x_1": invariants.shape_change(parent, candidate, source=source)}, shape)

    chained = [judged(parent, candidate) for parent, candidate in zip(steps, steps[1:])]
    assert chained[0]["pass"] and chained[0]["inherited_bends"]["lane:x_1"][0][2] == pytest.approx(-60.45)
    assert [row["witness_ids"] for row in chained[1:]] == [["heading:lane:x_1"]] * 11
    # Returning towards the goal source's bend is inherited again; a lane the goal source lacks inherits nothing.
    assert judged(steps[1], [BEND[0], BEND[1], (-20.0, 0.0), BEND[2]])["pass"]
    assert judged(BEND, [BEND[0], BEND[1], (-20.0, 0.0), BEND[2]], source=None)["witness_ids"] == ["heading:lane:x_1"]
    # Attack: a turn within the limit on the goal source (34.6 deg, limit 35) driven past it (35.05 deg) was exempt in
    # one step. Only a violation the goal source already has is inherited.
    gentle = [(-50.0 - 75.0, -75.0 * math.tan(math.radians(34.6))), (-50.0, 0.0), (-5.0, 0.0)]
    assert shape["max_heading_change_deg"] == 35
    over = judged(gentle, _sharpened(0.45, base=gentle), source=gentle)
    assert over["witness_ids"] == ["heading:lane:x_1"] and over["inherited_bends"] == {}
    assert judged(gentle, _sharpened(0.3, base=gentle), source=gentle)["pass"]


def test_d3_a_judged_chain_cannot_drift_an_inherited_bend(tmp_path):
    bent = _x4()
    _lane(bent, "W_in_1").set("shape", " ".join(f"{x},{y}" for x, y in BEND))
    source = copy.deepcopy(bent)
    for lane in ("W_in_0", "W_in_2"):
        _lane(source, lane).set("speed", "10")
    built = _goal(tmp_path, source, reference=bent, mutable_edge_ids=["W_in"])
    parent_profile, parent_net, current, results = _written(built, tmp_path / "source"), None, source, []
    for step, lane in enumerate(("W_in_0", "W_in_2"), start=1):
        candidate = copy.deepcopy(current)
        _lane(candidate, lane).set("speed", "13.89")
        _lane(candidate, "W_in_1").set("shape", " ".join(f"{x:.6f},{y:.6f}" for x, y in _sharpened(0.45 * step)))
        operation = {"id": "op", "operation": "set_speed", "params": {"lane_id": lane, "speed_m_s": 13.89}}
        profile = _judge(tmp_path, built, candidate, operation=operation, seeds=True, l2=True,
                         parent_profile=parent_profile, parent_net=parent_net, out=tmp_path / f"s{step}")
        results.append(profile)
        parent_profile, parent_net, current = (tmp_path / f"s{step}" / "profile.full.json",
                                               Path(profile["inputs"]["net"]["path"]), candidate)
    # Step 1 keeps the goal source's bend within 0.45 deg: inherited, accepted. Step 2 is 0.45 deg from its parent
    # but 0.9 deg from the goal source: bounded.
    assert _failed(results[0]) == {} and results[0]["gate"]["screening"] == "improved"
    assert _failed(results[1]) == {"INV-SHAPE": ["heading:lane:W_in_1"]}
    assert results[1]["gate"]["screening"] == "g0_rejected"


def test_inv_shape_judges_road_lanes_not_walking_areas_or_crossings():
    # Review finding (D3 control): an honest verify-edit set_lane_width next to a sidewalk on Ring1 moved walking-area
    # corners 0.024-0.401 m with unchanged turns; INV-SHAPE judged those outlines as road lanes (heading and direction
    # witnesses) and rejected the candidate. They are junction-derived, like internal lanes.
    ring1 = ET.parse(RING1).getroot()
    function = {lane.get("id"): edge.get("function") for edge in ring1.findall("edge") for lane in edge.findall("lane")}
    pedestrian = sorted(lane for lane, kind in function.items() if kind in {"walkingarea", "crossing"})
    assert {function[lane] for lane in pedestrian} == {"walkingarea", "crossing"}
    candidate = copy.deepcopy(ring1)
    for lane in candidate.iter("lane"):
        if lane.get("id") in pedestrian:
            points = [list(point) for point in _shape(candidate, lane.get("id"))]
            points[0][0] += 0.4
            lane.set("shape", " ".join(f"{x:.2f},{y:.2f}" for x, y in points))
    assert set(build_profile._road_lane_shapes(ring1)) == {lane for lane, kind in function.items() if kind is None}
    assert build_profile.shape_changes(ring1, candidate, {}, 3.0) == {}
    # A road lane in the same pair is still judged.
    road = "-1189704921_1"
    assert function[road] is None
    points = _shape(candidate, road)
    _lane(candidate, road).set("shape", " ".join(f"{x:.2f},{y + 4.0:.2f}" for x, y in points))
    changes = build_profile.shape_changes(ring1, candidate, {}, 3.0)
    assert list(changes) == [f"lane:{road}"]
    assert invariants.inv_shape(changes, dict(goal.DEFAULT_G0_SHAPE))["witness_ids"] == [f"hausdorff:lane:{road}"]


def test_d3_on_a_judged_candidate(tmp_path):
    bent = _x4()
    _lane(bent, "W_in_1").set("shape", " ".join(f"{x},{y}" for x, y in BEND))
    built = _goal(tmp_path, bent, reference=bent, mutable_edge_ids=["W_in"])

    def shape_witnesses(points) -> list[str]:
        candidate = copy.deepcopy(bent)
        _lane(candidate, "W_in_1").set("shape", " ".join(f"{x},{y}" for x, y in points))
        return _failed(_judge(tmp_path, built, candidate)).get("INV-SHAPE", [])

    assert shape_witnesses([(-75.0, -43.30127), (-50.0, 0.0), (-20.0, 0.0), (-5.0, 0.0)]) == []
    assert shape_witnesses([(-75.0, -43.30127), (-50.0, 0.0), (-7.0, 0.0),
                            (-5.0, 2 * math.tan(math.radians(40)))]) == ["heading:lane:W_in_1"]
    assert shape_witnesses([(-75.0, -43.30127), (-50.05, 0.0), (-5.0, 0.0)]) == ["heading:lane:W_in_1"]


def _gap(root: ET.Element, lane_id: str = ":J0_0_0", offset: float = 2.0) -> ET.Element:
    """A copy of ``root`` whose internal lane ``lane_id`` ends ``offset`` metres off its exit lane (a connection-mode
    path_endpoint_gap structural failure)."""
    result = copy.deepcopy(root)
    points = _shape(result, lane_id)
    points[-1] = [points[-1][0], points[-1][1] - offset]
    _lane(result, lane_id).set("shape", " ".join(f"{x},{y}" for x, y in points))
    return result


GAP_W = "J0:connection_mode:path_endpoint_gap:W_in_0>S_out_0:exit"
GAP_E = "J0:connection_mode:path_endpoint_gap:E_in_2>S_out_2:exit"


def _materialize(profile: dict) -> dict:
    return next(row for row in profile["invariants"] if row["id"] == "INV-MATERIALIZE")


def test_d1_a_clean_source_still_requires_zero_structural_failures(tmp_path):
    clean = _x4()
    built = _goal(tmp_path, clean)
    gapped = _gap(clean)
    assert build_profile.structural_failures(clean) == [] and build_profile.structural_failures(gapped) == [
        (GAP_W, 2.0)]
    profile = _judge(tmp_path, built, gapped, structural=1)
    materialize = _materialize(profile)
    assert not materialize["pass"] and profile["gate"]["G0"] == "fail"
    assert materialize["witness_ids"] == sorted([f"structure:{GAP_W}", "structure:count:1"])
    assert (materialize["decision"], materialize["source_dirty"], materialize["inherited"]) == ("D1", False, [])
    assert profile["source_dirty"] is False and profile["source_structure"]["count"] == 0
    assert "promotion" not in profile["source_structure"] and "Dirty source" not in profile["claim"]
    assert "promotion" not in json.loads(render.render_build_compact(profile)[0])["source_structure"]
    # Zero failures pass; verify-edit's count must still equal the recount.
    assert _failed(_judge(tmp_path, built, copy.deepcopy(clean))) == {}
    assert _failed(_judge(tmp_path, built, copy.deepcopy(clean), structural=2))["INV-MATERIALIZE"] == [
        "structure:count:2", "structure:recount:0"]


def test_d1_a_dirty_source_accepts_inherited_unchanged_failures_and_blocks_promotion(tmp_path, monkeypatch):
    # Decision D1: the design's "structural failures 0" left every Ring1 repair failing on the source's own 5 gaps.
    reference = _gap(_x4())
    source = copy.deepcopy(reference)
    _lane(source, "W_in_1").set("speed", "10")
    built = _goal(tmp_path, source, reference=reference)
    assert "not promotable without a human" in built[0].claim_boundary
    speed = {"id": "op", "operation": "set_speed", "params": {"lane_id": "W_in_1", "speed_m_s": 13.89}}
    repaired = copy.deepcopy(source)
    _lane(repaired, "W_in_1").set("speed", "13.89")
    work = tmp_path / f"judge-{len(list(tmp_path.glob('judge-*')))}"
    profile = _judge(tmp_path, built, repaired, operation=speed, structural=1, seeds=True, l2=True)
    assert _failed(profile) == {} and profile["gate"]["screening"] == "improved"
    # Attack: the CLI summary printed status measured and screening improved with no trace of the promotion block.
    monkeypatch.setattr(sys, "dont_write_bytecode", sys.dont_write_bytecode)
    code, summary = _run(_cli(), ["profile", "build", "--goal", str(next((tmp_path / "store").rglob("goal.json"))),
                                  "--verify-edit", str(work), "--movement-guard", str(work / "movement-guard"),
                                  "--out", str(tmp_path / "cli"), "--json"])
    assert (code, summary["status"], summary["screening"]) == (0, "measured", "improved")
    assert summary["source_dirty"] is True and summary["source_structure"]["count"] == 1
    assert summary["source_structure"]["promotion"].startswith("blocked without a human")
    assert "not promotable without a human" in summary["claim"]
    compact = json.loads((tmp_path / "cli" / "profile.compact.json").read_text(encoding="utf-8"))
    assert compact["source_structure"]["promotion"] == "blocked without a human (D1)"
    materialize = _materialize(profile)
    assert materialize["source_dirty"] and materialize["inherited"] == [{"id": GAP_W, "measure": 2.0}]
    assert profile["source_dirty"] is True and profile["source_structure"]["inherited"] == materialize["inherited"]
    assert profile["source_structure"]["promotion"].startswith("blocked without a human")
    assert "not promotable without a human" in profile["claim"]
    assert profile["binding_decisions"]["D1"]["invariant"] == "INV-MATERIALIZE" and "proposals" not in profile
    assert "SOURCE dirty: 1 structural failure(s)" in render.render_build_compact(profile)[1]
    # The goal source's own profile carries the flag too.
    source_profile = _profile(built)
    assert source_profile["source_dirty"] and source_profile["source_structure"]["inherited"] == materialize["inherited"]
    # A verify-edit count that is not the recount stays a witness on a dirty source.
    assert _failed(_judge(tmp_path, built, repaired, operation=speed, structural=0))["INV-MATERIALIZE"] == [
        "structure:recount:1"]


def test_d1_a_dirty_source_rejects_a_new_failure_and_any_growth(tmp_path):
    source = _gap(_x4())
    built = _goal(tmp_path, source, reference=source)
    more = _gap(source, ":J0_5_0")
    assert build_profile.structural_failures(more) == [(GAP_E, 2.0), (GAP_W, 2.0)]
    assert _failed(_judge(tmp_path, built, more, structural=2))["INV-MATERIALIZE"] == [f"structure:{GAP_E}"]
    grown = _gap(_x4(), offset=2.5)
    assert build_profile.structural_failures(grown) == [(GAP_W, 2.5)]
    assert _failed(_judge(tmp_path, built, grown, structural=1))["INV-MATERIALIZE"] == [f"worse:{GAP_W}"]
    # Any growth of an inherited measured finding counts: the numerical epsilon is no tolerance.
    assert invariants.measured_regressions([("gap", 2.0)], [("gap", 2.001)]) == ([], ["gap"])
    assert invariants.measured_regressions([("gap", 2.0)], [("gap", 2.0)]) == ([], [])


def test_d1_a_source_whose_failures_all_sit_on_removed_lanes_stays_dirty(tmp_path, monkeypatch):
    # Review finding: when the composed lane remap removed every lane the goal source's failures name, INV-MATERIALIZE
    # derived its flag from the empty renamed list and said "clean goal source" while the profile said dirty.
    common = {"report_status": "measured", "candidate_sha256": "a", "declared_sha256": "a", "load_status": "pass",
              "source_immutable": True, "source_sha256": "p", "parent_sha256": "p"}
    row = invariants.inv_materialize(structural_failures=0, structure={"source": [], "candidate": []},
                                     source_dirty=True, **common)
    assert row["pass"] and row["source_dirty"] is True and row["inherited"] == []
    assert "dirty source" in row["reason"] and "clean goal source" not in row["reason"]
    # With nothing inherited, any candidate failure is new.
    gapped = invariants.inv_materialize(structural_failures=1, structure={"source": [], "candidate": [(GAP_W, 2.0)]},
                                        source_dirty=True, **common)
    assert gapped["witness_ids"] == [f"structure:{GAP_W}"]
    # The profile passes the goal source's own flag, not the renamed list's.
    reference = _gap(_x4())
    source = copy.deepcopy(reference)
    _lane(source, "W_in_1").set("speed", "10")
    built = _goal(tmp_path, source, reference=reference)
    original = build_profile._source_structure
    monkeypatch.setattr(build_profile, "_source_structure",
                        lambda *args: (original(*args)[0], []))
    speed = {"id": "op", "operation": "set_speed", "params": {"lane_id": "W_in_1", "speed_m_s": 13.89}}
    repaired = _gap(_x4())
    profile = _judge(tmp_path, built, repaired, operation=speed, structural=1)
    materialize = _materialize(profile)
    assert profile["source_dirty"] is True and materialize["source_dirty"] is True
    assert materialize["witness_ids"] == [f"structure:{GAP_W}"] and "clean goal source" not in materialize["reason"]


def test_d1_a_chain_cannot_add_a_failure_the_goal_source_lacks(tmp_path):
    reference = _gap(_x4())
    source = copy.deepcopy(reference)
    _lane(source, "W_in_1").set("speed", "10")
    _lane(source, "S_in_0").set("width", "3.5")
    built = _goal(tmp_path, source, reference=reference, mutable_edge_ids=["W_in", "S_in"])
    speed = {"id": "op", "operation": "set_speed", "params": {"lane_id": "W_in_1", "speed_m_s": 13.89}}
    first = copy.deepcopy(source)
    _lane(first, "W_in_1").set("speed", "13.89")
    step1 = _judge(tmp_path, built, first, operation=speed, structural=1, seeds=True, l2=True,
                   parent_profile=_written(built, tmp_path / "source"), out=tmp_path / "s1")
    assert step1["gate"]["screening"] == "improved" and step1["source_dirty"]
    width = {"id": "op", "operation": "set_lane_width", "params": {"lane_id": "S_in_0", "width_m": 3.2}}
    chained = {"operation": width, "plan": {"mutable_junction_ids": ["J0", "S"], "mutable_edge_ids": ["S_in"]},
               "seeds": True, "l2": True, "parent_net": Path(step1["inputs"]["net"]["path"]),
               "parent_profile": tmp_path / "s1" / "profile.full.json"}
    second = copy.deepcopy(first)
    _lane(second, "S_in_0").set("width", "3.2")
    good = _judge(tmp_path, built, second, structural=1, **chained)
    assert _failed(good) == {} and good["gate"]["screening"] == "improved" and good["V"]["cand"][:4] == [0, 0, 0, 0]
    # The parent is a candidate, so the baseline is read from the hash-checked goal source, not the parent.
    assert good["inputs"]["parent_net"]["sha256"] != built[0].source.sha256
    assert _materialize(good)["inherited"] == [{"id": GAP_W, "measure": 2.0}]
    # Step 2 adds a failure the goal source lacks: rejected.
    broken = _gap(second, ":J0_5_0")
    bad = _judge(tmp_path, built, broken, structural=2, **chained)
    assert f"structure:{GAP_E}" in _failed(bad)["INV-MATERIALIZE"] and bad["gate"]["G0"] == "fail"


def test_run_verify_edit_runs_the_goal_frozen_trips_with_the_parent_effective_facts(tmp_path, monkeypatch):
    # Review finding (Missing-1): verify-edit regenerated each seed's requests from the ledger source and the twin
    # only compared hashes afterwards; the goal's frozen trip files are now the source audit's input.
    from astra_sumo.core import network_edit_applicability

    built = _goal(tmp_path, _x4(), seeds=[42, 17])
    loaded, _, _, facts_path, _ = built
    seen = {}
    monkeypatch.setattr(network_edit_applicability, "verify_network_edit", lambda **kwargs: seen.update(kwargs) or {})
    build_profile.run_verify_edit(loaded, ledger_file=tmp_path / "ledger.json", facts_path=facts_path,
                                  output_dir=tmp_path / "ve", repository_root=ROOT)
    trips = {row.seed: row.sha256 for row in loaded.guards.routeability.trips}
    assert seen["routeability_seeds"] == (42, 17) and sorted(seen["routeability_trip_files"]) == [17, 42]
    assert {seed: file_sha(path) for seed, path in seen["routeability_trip_files"].items()} == trips
    assert seen["facts_file"] == facts_path
    build_profile.run_verify_edit(loaded, ledger_file=tmp_path / "ledger.json", facts_path=facts_path,
                                  output_dir=tmp_path / "ve2", repository_root=ROOT, routeability_seeds=(5,))
    assert seen["routeability_trip_files"] is None
    tampered = next(iter({seed: path for seed, path in
                          build_profile.frozen_trip_files(loaded, facts_path, (42,)).items()}.values()))
    tampered.write_bytes(tampered.read_bytes() + b"\n")
    with pytest.raises(ValueError, match="frozen trips of seed 42"):
        build_profile.run_verify_edit(loaded, ledger_file=tmp_path / "ledger.json", facts_path=facts_path,
                                      output_dir=tmp_path / "ve3", repository_root=ROOT)


def _map_facts(table: dict, locks: dict[str, str]) -> dict:
    """Hamburg-producer style lane facts ``M:lane:<MAP lane>:...`` locked on the given SUMO lanes."""
    rows = [dict(fact) for fact in table["facts"]]
    for map_lane, sumo_lane in locks.items():
        stop = next(fact for fact in table["facts"] if fact["id"] == f"lane:{sumo_lane}:stop_line")
        rows.append({**stop, "id": f"M:lane:{map_lane}:stop_line"})
    return {**table, "facts": rows}


def test_sg_partition_checks_evidenced_map_groups_only(tmp_path, monkeypatch):
    # Review finding (Missing-2): the evidenced sg_partition colour check was absent.
    root = _x4()
    connections = [{"node_id": "M", "connection_id": "1", "ingress_lane_id": "w1", "egress_lane_id": "e1",
                    "signal_group": "2"},
                   {"node_id": "M", "connection_id": "2", "ingress_lane_id": "s0", "egress_lane_id": "e0",
                    "signal_group": "2"},
                   {"node_id": "M", "connection_id": "3", "ingress_lane_id": "w0", "egress_lane_id": "s9",
                    "signal_group": "5"}]
    table = build_profile.parse_table(json.dumps(_map_facts(_facts_for(tmp_path, root), {
        "w1": "W_in_1", "e1": "E_out_1", "s0": "S_in_0", "e0": "E_out_0", "w0": "W_in_0"})).encode())
    locks = signal_partition.map_lane_locks(table)
    assert locks == {("M", "e0"): "E_out_0", ("M", "e1"): "E_out_1", ("M", "s0"): "S_in_0",
                     ("M", "w0"): "W_in_0", ("M", "w1"): "W_in_1"}
    bindings = signal_partition.evidenced_bindings(connections, locks)
    # Connection 3 has an unlocked egress lane (s9): not evidenced.
    assert [row["connection_id"] for row in bindings] == ["1", "2"]
    logic = root.find("tlLogic")
    for phase, state in zip(logic.findall("phase"), ("rGrrrrGrrrrr", "rrrrrrrrrrrr")):
        phase.set("state", state)
    consistent = signal_partition.check(root, bindings)
    assert consistent["status"] == "pass" and consistent["rows"]["sg:M:2"]["status"] == "matched"
    # G against g is priority, not colour; G against r in one phase is a conflict.
    logic.findall("phase")[0].set("state", "rGrrrrgrrrrr")
    assert signal_partition.check(root, bindings)["status"] == "pass"
    logic.findall("phase")[0].set("state", "rGrrrrrrrrrr")
    conflict = signal_partition.check(root, bindings)
    assert conflict["status"] == "inconsistent" and conflict["groups"][0]["conflicts"] == [
        {"program": logic.get("programID"), "phase": 0, "states": {"S_in_0>E_out_0": "r", "W_in_1>E_out_1": "G"}}]
    # o, O and s need a declared mapping: that phase is unscored, never a conflict.
    logic.findall("phase")[0].set("state", "rGrrrrOrrrrr")
    unscored = signal_partition.check(root, bindings)
    assert unscored["status"] == "pass" and unscored["groups"][0]["unscored_phases"] == 1
    # No evidenced binding (the Ring1 reference nets): not_applicable, never pass.
    assert signal_partition.check(root, [])["status"] == "not_applicable"
    built = _goal(tmp_path, _x4())
    assert _profile(built)["sg_partition"]["status"] == "not_applicable"
    # A goal with MAP connections ranks an inconsistent group as a CTL hotspot (never in V).
    logic.findall("phase")[0].set("state", "rGrrrrrrrrrr")
    from astra_sumo.core import digital_twin

    monkeypatch.setattr(digital_twin, "parse_mapem", lambda _: ([], [digital_twin.MapConnection(
        row["node_id"], row["connection_id"], row["ingress_lane_id"], row["egress_lane_id"], row["signal_group"], "")
        for row in connections]))
    map_xml = tmp_path / "map.xml"
    map_xml.write_bytes(b"<MapData/>")
    built = _goal(tmp_path / "map", root, map_xml=map_xml, facts=_map_facts(_facts_for(tmp_path, root), {
        "w1": "W_in_1", "e1": "E_out_1", "s0": "S_in_0", "e0": "E_out_0"}))
    frozen = built[0].sg_partition
    assert [row.connection_id for row in frozen.connections] == ["1", "2", "3"]
    assert frozen.map_xml.sha256 == file_sha(map_xml)
    profile = _profile(built)
    assert profile["sg_partition"]["status"] == "inconsistent" and "H:CTL:tls:J0" in profile["hotspot_ids"]
    assert profile["V"]["cand"][1] == 0


# ---- attack round regressions (shape direction, added lanes, audit remap, parent re-derivation, sg, L2) ------------

def _loop_with_parent_vertex_count() -> tuple[list, list]:
    """A straight 21-vertex parent and a candidate with 21 vertices that loops 360 deg (radius 0.15 m, 30 deg steps)
    at x = 50 and is straight elsewhere."""
    parent = [(float(x), 0.0) for x in range(0, 101, 5)]
    radius = 0.15
    loop = [(50 + radius * math.cos(-math.pi / 2 + k * math.pi / 6),
             radius + radius * math.sin(-math.pi / 2 + k * math.pi / 6)) for k in range(12)]
    left, middle, right = [(0.0, 0.0), (25.0, 0.0), (45.0, 0.0)], loop + [(50.0, 0.0)], [(75.0, 0.0), (100.0, 0.0)]
    pad = len(parent) - len(left) - len(middle) - len(right)
    return parent, left + middle + [(55.0 + 18.0 * (i + 1) / (pad + 1), 0.0) for i in range(pad)] + right


def test_inv_shape_rejects_a_lane_that_loops_or_runs_reversed_within_every_turn_limit():
    # Attack finding: a 360 deg loop of radius 0.15 m in 30 deg steps that kept the parent's 21 vertices passed
    # (vertex_delta 0, max turn 30 deg, Hausdorff 0.3 m), and so did a lane reversed vertex for vertex.
    shape = dict(goal.DEFAULT_G0_SHAPE)
    parent, looped = _loop_with_parent_vertex_count()
    change = invariants.shape_change(parent, looped)
    assert (len(looped), change["vertex_delta"], round(change["max_turn_candidate_deg"], 6)) == (21, 0, 30.0)
    assert change["directed_hausdorff_bound_m"] < shape["max_directed_hausdorff_m"]
    assert change["backward_m"] == pytest.approx(0.3, abs=0.01) and change["backward_samples"] > 0
    assert invariants.inv_shape({"lane:x_0": change}, shape)["witness_ids"] == ["direction:lane:x_0"]
    reversed_lane = invariants.shape_change(parent, parent[::-1])
    assert reversed_lane["directed_hausdorff_m"] == 0 and reversed_lane["vertex_delta"] == 0
    assert invariants.inv_shape({"lane:x_0": reversed_lane}, shape)["witness_ids"] == ["direction:lane:x_0"]
    # Forward shapes never fall behind: straightening a jittered lane, the jitter itself, a corner cut.
    jitter = [(5.0 * i, 0.0 if i in (0, 20) else (1.0 if i % 2 else -1.0)) for i in range(21)]
    assert invariants.shape_change(jitter, [(0.0, 0.0), (100.0, 0.0)])["backward_m"] == 0
    assert invariants.shape_change([(0.0, 0.0), (100.0, 0.0)], jitter)["backward_m"] == 0
    corner = invariants.shape_change([(0.0, -50.0), (0.0, 0.0), (50.0, 0.0)],
                                     [(0.0, -50.0), (0.0, -2.0), (2.0, 0.0), (50.0, 0.0)])
    assert corner["backward_m"] == pytest.approx(0.0, abs=1e-9)


def test_inv_shape_judges_the_distance_bound_so_no_excess_hides_between_samples():
    # Attack finding: sampled every 1 m, a candidate cutting the inner side of a 90 deg parent bend (every turn 22.5
    # deg) measured 2.9988 m while its true distance is 3.3398 m, and passed the 3.0 m limit.
    parent = [(0.0, -50.0), (0.0, 0.0), (50.0, 0.0)]
    candidate = [(0.0, -50.0), (0.0, -9.9553), (2.3152, -4.3658), (4.3658, -2.3152), (9.9553, 0.0), (50.0, 0.0)]
    assert invariants.directed_hausdorff_m(candidate, parent, 1.0) == pytest.approx(2.9988, abs=1e-3)
    exact = invariants.directed_hausdorff_m(candidate, parent, 0.001)
    assert exact == pytest.approx(3.3398, abs=1e-3)
    change = invariants.shape_change(parent, candidate)
    assert change["spacing_m"] == invariants.HAUSDORFF_SPACING_M
    assert change["directed_hausdorff_m"] <= exact <= change["directed_hausdorff_bound_m"]
    assert change["max_turn_candidate_deg"] == pytest.approx(22.5, abs=0.01)
    result = invariants.inv_shape({"lane:x_0": change}, dict(goal.DEFAULT_G0_SHAPE))
    assert result["witness_ids"] == ["hausdorff:lane:x_0"]
    # Near the limit the bound is refined to the 1 mm resolution: a stop line restored 3.0 m further along the lane
    # (K2 stop_line), or a bend reaching exactly 3.0 m off the parent, passes; 3.01 m does not.
    shape = dict(goal.DEFAULT_G0_SHAPE)
    for parent_shape, candidate, passes in (([(0.0, 0.0), (97.0, 0.0)], [(0.0, 0.0), (97.0, 0.0), (100.0, 0.0)], True),
                                            ([(0.0, 0.0), (100.0, 0.0)], [(0.0, 0.0), (50.0, 3.0), (100.0, 0.0)], True),
                                            ([(0.0, 0.0), (100.0, 0.0)], [(0.0, 0.0), (50.0, 3.01), (100.0, 0.0)],
                                             False)):
        coarse = invariants.shape_change(parent_shape, candidate)
        refined = invariants.shape_change(parent_shape, candidate, limit_m=shape["max_directed_hausdorff_m"])
        assert refined["directed_hausdorff_bound_m"] <= coarse["directed_hausdorff_bound_m"]
        exact = invariants.directed_hausdorff_m(candidate, parent_shape, 0.001)
        assert exact <= refined["directed_hausdorff_bound_m"] <= exact + invariants.HAUSDORFF_RESOLUTION_M
        assert invariants.inv_shape({"lane:x_0": refined}, shape)["pass"] is passes


def _densified(root: ET.Element, lane_id: str, count: int = 21) -> None:
    (x0, y0), (x1, y1) = [tuple(point) for point in _shape(root, lane_id)]
    _lane(root, lane_id).set("shape", " ".join(f"{x0 + (x1 - x0) * k / (count - 1):.4f},"
                                               f"{y0 + (y1 - y0) * k / (count - 1):.4f}" for k in range(count)))


def test_g0_rejects_a_looped_or_reversed_lane_in_a_full_profile(tmp_path):
    # Attack finding: a set_edge_shape repair of three offset W_in centerlines whose W_in_1 carries a same-vertex-count
    # loop screened improved with G0 pass; a reversed W_in_1 was rejected only through other rules.
    reference = _x4()
    for lane in ("W_in_0", "W_in_1", "W_in_2"):
        _densified(reference, lane)
    source = copy.deepcopy(reference)
    for lane in ("W_in_0", "W_in_1", "W_in_2"):
        points = _shape(reference, lane)
        _lane(source, lane).set("shape", " ".join(f"{x:.4f},{y + (1.0 if 0 < i < len(points) - 1 else 0.0):.4f}"
                                                  for i, (x, y) in enumerate(points)))
    built = _goal(tmp_path, source, reference=reference)
    honest = _judge(tmp_path, built, copy.deepcopy(reference), seeds=True, l2=True)
    assert honest["gate"]["screening"] == "improved", honest["gate"]["first_fail"]
    points = [tuple(point) for point in _shape(reference, "W_in_1")]
    radius, centre = 0.15, points[10][0]
    loop = [(centre + radius * math.cos(-math.pi / 2 + k * math.pi / 6),
             radius + radius * math.sin(-math.pi / 2 + k * math.pi / 6)) for k in range(12)]
    left, right = [points[0], points[4], points[8], (centre - 2.0, 0.0)], [points[13], points[16], points[20]]
    pad = len(points) - len(left) - len(loop) - 1 - len(right)
    looped_shape = left + loop + [(centre, 0.0)] + [(centre + 0.5 * (i + 1), 0.0) for i in range(pad)] + right
    assert len(looped_shape) == len(points)
    looped = copy.deepcopy(reference)
    _lane(looped, "W_in_1").set("shape", " ".join(f"{x:.4f},{y:.4f}" for x, y in looped_shape))
    rejected = _judge(tmp_path, built, looped, seeds=True, l2=True)
    assert rejected["gate"]["screening"] == "g0_rejected" and rejected["V"]["cand"] == [0, 0, 0, 0, 0.0]
    assert _failed(rejected) == {"INV-SHAPE": ["direction:lane:W_in_1"]}
    reversed_net = copy.deepcopy(reference)
    _lane(reversed_net, "W_in_1").set("shape", " ".join(f"{x:.4f},{y:.4f}" for x, y in reversed(points)))
    assert "direction:lane:W_in_1" in _failed(_judge(tmp_path, built, reversed_net, seeds=True, l2=True))["INV-SHAPE"]


def _lane_count_goal(tmp_path: Path, source: ET.Element, *, kinds=("lane_width", "lane_count")):
    """A goal on E_out (and J0) whose reference expects one bicycle lane the source lacks, and its source profile."""
    table = _facts_for_kinds(tmp_path, source, kinds, ("E_out",))
    next(fact for fact in table["facts"] if fact["kind"] == "lane_count")["expected"] = 1
    built = _goal(tmp_path, source, facts=table, mutable_edge_ids=["E_out"], mutable_junction_ids=["J0"])
    return built, _written(built, tmp_path / "source")


ADD_RIGHT = {"id": "op", "operation": "set_lane_count", "params": {"edge_id": "E_out", "num_lanes": 4,
                                                                   "add_side": "right"}}
ADD_RIGHT_PLAN = {"mutable_junction_ids": ["E", "J0"], "mutable_edge_ids": ["E_out"],
                  "lane_index_mapping": {"0": 1, "1": 2, "2": 3}}


def test_g0_bounds_an_added_lane_against_its_edge_and_its_direction(tmp_path):
    # Attack finding: a lane added by set_lane_count 40 m off the edge passed INV-SHAPE with no witness (an added lane
    # was judged on its turns only), and a later step reversing that connection-less lane passed G0.
    built, source_profile = _lane_count_goal(tmp_path, _x4(), kinds=("lane_width", "lane_count", "lane_centerline"))
    far = _judge(tmp_path, built, _add_right_lane(_x4(), "E_out", offset=40.0), operation=ADD_RIGHT,
                 plan=ADD_RIGHT_PLAN, seeds=True, l2=True, parent_profile=source_profile)
    assert far["gate"]["screening"] == "g0_rejected" and _failed(far) == {"INV-SHAPE": ["hausdorff:lane:E_out_0"]}
    near = _add_right_lane(_x4(), "E_out")
    step1 = _judge(tmp_path, built, near, operation=ADD_RIGHT, plan=ADD_RIGHT_PLAN, seeds=True, l2=True,
                   parent_profile=source_profile, out=tmp_path / "s1")
    assert step1["gate"]["screening"] == "improved", step1["gate"]["first_fail"]
    reverse = copy.deepcopy(near)
    _lane(reverse, "E_out_0").set("shape", " ".join(f"{x},{y}" for x, y in reversed(_shape(near, "E_out_0"))))
    step2 = _judge(tmp_path, built, reverse, operation={"id": "op", "operation": "set_edge_shape", "params": {
        "edge_id": "E_out", "shape": [[5, 0], [100, 0]]}}, plan={"mutable_edge_ids": ["E_out"]}, seeds=True, l2=True,
        parent_net=Path(step1["inputs"]["net"]["path"]), parent_profile=tmp_path / "s1" / "profile.full.json")
    assert step2["gate"]["screening"] == "g0_rejected" and _failed(step2) == {"INV-SHAPE": ["direction:lane:E_out_0"]}


def test_inv_audit_compares_findings_across_the_declared_lane_remap(tmp_path):
    # Attack finding: with a request self_foe on link 6 (S_in_0>E_out_0), adding a right lane on E_out left the same
    # physical finding keyed S_in_0>E_out_1, and INV-AUDIT called it new; the same step without it screened improved.
    source = _x4()
    request = next(row for row in source.find("junction[@id='J0']").findall("request") if row.get("index") == "6")
    foes = list(request.get("foes"))
    foes[len(foes) - 1 - 6] = "1"
    request.set("foes", "".join(foes))
    built, source_profile = _lane_count_goal(tmp_path, source)
    inherited = json.loads(source_profile.read_text(encoding="utf-8"))["int_findings"]
    assert [row["id"] for row in inherited] == ["request:J0:self_foe:S_in_0>E_out_0"]
    step = _judge(tmp_path, built, _add_right_lane(source, "E_out"), operation=ADD_RIGHT, plan=ADD_RIGHT_PLAN,
                  seeds=True, l2=True, parent_profile=source_profile)
    assert step["gate"]["screening"] == "improved" and _failed(step) == {}, step["gate"]["first_fail"]
    row = next(row for row in step["int_findings"] if row["id"] == "request:J0:self_foe:S_in_0>E_out_1")
    assert row["new_vs_parent"] is False
    # The remap also unmasks: a raw key that collides with a parent identity after the rename is new.
    mapping = {"E_out": {"0": "1", "1": "2", "2": "3"}}
    parent = [{"id": "request:J0:self_foe:W_in_1>E_out_1", "source": "request_matrix", "measure": None, "lanes": []},
              {"id": "audit:J0:connection_mode:incoming_motorized_lane_without_connection:E_out:2",
               "source": "connection_mode_audit", "measure": None, "lanes": []},
              {"id": "audit:J0:connection_mode:path_endpoint_gap:S_in_0>E_out_0:exit", "source": "connection_mode_audit",
               "measure": 0.5, "lanes": []}]
    renamed = junction_integrity.comparable(parent, mapping, remap=True)
    assert renamed == [("request:J0:self_foe:W_in_1>E_out_2", None),
                       ("audit:J0:connection_mode:incoming_motorized_lane_without_connection:E_out:3", None),
                       ("audit:J0:connection_mode:path_endpoint_gap:S_in_0>E_out_1:exit", 0.5)]
    collided = invariants.inv_audit(renamed, [("request:J0:self_foe:W_in_1>E_out_1", None)], ["J0"])
    assert collided["witness_ids"] == ["request:J0:self_foe:W_in_1>E_out_1"]
    assert junction_integrity.remap_identity("request:J0:self_foe:W_in_1>E_out_1", {"E_out": {"0": "0"}}) is None
    assert junction_integrity.remap_identity("audit:J:connection_mode:crossed_lane_mapping:A:E_out:0>1:1>0",
                                             {"E_out": {"0": "1", "1": "2"}}) == (
        "audit:J:connection_mode:crossed_lane_mapping:A:E_out:0>2:1>1")
    # Safety identities hash lane ordinals from the curb; on a remapped edge they compare by category and lanes.
    safety = [{"id": "safety:J0:protected_conflict:0123456789ab", "source": "independent_safety", "measure": None,
               "lanes": ["E_out_0", "S_in_0"]}]
    assert junction_integrity.comparable(safety, mapping, remap=True) == [
        ("safety:J0:protected_conflict:lanes:E_out_1|S_in_0", None)]
    assert junction_integrity.comparable(safety, {}, remap=True) == [("safety:J0:protected_conflict:0123456789ab",
                                                                      None)]
    # Only where surviving lanes change their ordinal from the curb: removing the leftmost lane of a right-hand edge
    # keeps every hash (K2 lane_count, whose restored finding equals the frozen baseline's), adding a right lane or
    # removing the leftmost lane in left-hand traffic does not.
    four = _add_right_lane(_x4(), "E_out")
    assert build_profile.curb_renumbered({"E_out": {"0": "0", "1": "1", "2": "2"}}, four, _x4()) == set()
    assert build_profile.curb_renumbered(mapping, _x4(), four) == {"E_out"}
    left_four, left_three = copy.deepcopy(four), _x4()
    for net in (left_four, left_three):
        net.set("lefthand", "true")
    assert build_profile.curb_renumbered({"E_out": {"0": "0", "1": "1", "2": "2"}}, left_four, left_three) == {
        "E_out"}
    assert junction_integrity.comparable(safety, mapping, remap=True, renumbered=set()) == [
        ("safety:J0:protected_conflict:0123456789ab", None)]
    # The goal baseline follows a chain of lane-count remaps (goal -> parent -> candidate).
    assert build_profile.compose_lane_mappings({"E_out": {"0": "1", "1": "2", "2": "3"}},
                                               {"E_out": {"0": "0", "1": "1", "3": "2"}}) == {
        "E_out": {"0": "1", "2": "2"}}


def _forged(profile_path: Path, out: Path, edit) -> Path:
    document = json.loads(profile_path.read_text(encoding="utf-8"))
    edit(document)
    out.mkdir(parents=True)
    (out / "profile.full.json").write_text(json.dumps(document), encoding="utf-8")
    return out / "profile.full.json"


def test_an_edited_parent_profile_is_refused_because_it_does_not_re_derive(tmp_path, monkeypatch):
    # Attack findings: the immediate parent was neither hash-bound nor re-verified, so an edited profile laundered a
    # G0-rejected candidate (gate edited to improved), an arbitrary net (inputs.net swapped with its own sha), a forged
    # effective fact table (a 0.7 m wrong width scored improved) or an unrun L2 (movement_guard pointed at "{}" or at
    # another candidate's record) into an accepted chain.
    from astra_sumo.core import network_edit_applicability

    clean = _x4()
    faulted = copy.deepcopy(clean)
    _lane(faulted, "E_out_2").set("speed", "10")
    _lane(faulted, "S_in_0").set("width", "3.5")
    built = _goal(tmp_path, faulted, reference=clean, mutable_edge_ids=["E_out", "S_in", "W_in"])
    source = _written(built, tmp_path / "source")
    speed = {"id": "op", "operation": "set_speed", "params": {"lane_id": "E_out_2", "speed_m_s": 13.89}}
    width = {"id": "op", "operation": "set_lane_width", "params": {"lane_id": "S_in_0", "width_m": 3.2}}
    first = copy.deepcopy(faulted)
    _lane(first, "E_out_2").set("speed", "13.89")
    step1 = _judge(tmp_path, built, first, operation=speed, seeds=True, l2=True, parent_profile=source,
                   out=tmp_path / "c1")
    assert step1["gate"]["screening"] == "improved"
    c1, first_net = tmp_path / "c1" / "profile.full.json", Path(step1["inputs"]["net"]["path"])
    second = copy.deepcopy(first)
    _lane(second, "S_in_0").set("width", "3.2")
    # The honest parent re-derives: the child is accepted and records the chain.
    honest = _judge(tmp_path, built, second, operation=width, seeds=True, l2=True, parent_net=first_net,
                    parent_profile=c1)
    assert honest["gate"]["screening"] == "improved" and honest["inputs"]["parent_profile"]["chain"] == [
        step1["cand"], json.loads(source.read_text(encoding="utf-8"))["cand"]]
    zigzag = copy.deepcopy(first)
    _lane(zigzag, "W_in_1").set("shape", " ".join(f"{-100 + 0.5 * n},{0.4 if n % 2 else 0.0}" for n in range(191)))
    bad = _judge(tmp_path, built, zigzag, operation=speed, seeds=True, l2=True, parent_profile=source,
                 out=tmp_path / "bad")
    assert bad["gate"]["screening"] == "g0_rejected"

    def accepted(document):
        document.update(status="measured")
        document["gate"].update(G0="pass", screening="improved", first_fail=None)

    forged_gate = _forged(tmp_path / "bad" / "profile.full.json", tmp_path / "forged-gate", accepted)
    with pytest.raises(ValueError, match="does not re-derive from its bound inputs"):
        _judge(tmp_path, built, zigzag, operation=width, seeds=True, l2=True,
               parent_net=Path(bad["inputs"]["net"]["path"]), parent_profile=forged_gate)
    arbitrary = _write(zigzag, tmp_path / "arbitrary.net.xml")

    def other_net(document):
        document["inputs"]["net"] = {"path": str(arbitrary), "sha256": file_sha(arbitrary)}

    with pytest.raises(ValueError, match="is not the candidate its hash-bound verify-edit report materialized"):
        _judge(tmp_path, built, zigzag, operation=width, parent_net=arbitrary,
               parent_profile=_forged(c1, tmp_path / "forged-net", other_net))
    table = json.loads(built[2])
    next(fact for fact in table["facts"] if fact["id"] == "lane:S_in_0:width")["expected"] = 3.9
    forged_table = tmp_path / "forged-facts.json"
    forged_table.write_text(json.dumps(table), encoding="utf-8")

    def other_facts(document):
        document["inputs"]["effective_facts"] = {"path": str(forged_table), "sha256": file_sha(forged_table),
                                                 "basis": "inherited", "verified": True, "remaps": 0}

    wrong = copy.deepcopy(first)
    _lane(wrong, "S_in_0").set("width", "3.9")
    with pytest.raises(ValueError, match="does not re-derive from its bound inputs"):
        _judge(tmp_path, built, wrong, operation={**width, "params": {"lane_id": "S_in_0", "width_m": 3.9}},
               seeds=True, l2=True, parent_net=first_net, parent_profile=_forged(c1, tmp_path / "forged-facts",
                                                                                   other_facts))
    unprobed = _judge(tmp_path, built, first, operation=speed, seeds=True, parent_profile=source, out=tmp_path / "c0")
    assert unprobed["gate"]["screening"] == "l2_not_run"
    empty = tmp_path / "empty-guard.json"
    empty.write_text("{}", encoding="utf-8")
    for name, guard in (("empty", empty), ("other", Path(bad["inputs"]["movement_guard"]["path"]))):
        def unguarded(document, guard=guard):
            accepted(document)
            document["inputs"]["movement_guard"] = {"path": str(guard), "sha256": file_sha(guard)}

        forged = _forged(tmp_path / "c0" / "profile.full.json", tmp_path / f"forged-guard-{name}", unguarded)
        with pytest.raises(ValueError, match="movement-guard record of parent .* does not bind"):
            _judge(tmp_path, built, second, operation=width, parent_net=first_net, parent_profile=forged)
        # verify-edit takes the parent's effective table only from a verified parent.
        monkeypatch.setattr(network_edit_applicability, "verify_network_edit", lambda **kwargs: {})
        loaded, data, _, facts_path, _ = built
        with pytest.raises(ValueError, match="does not bind"):
            build_profile.run_verify_edit(loaded, ledger_file=tmp_path / "ledger.json", facts_path=facts_path,
                                          output_dir=tmp_path / f"ve-{name}", repository_root=ROOT,
                                          parent_profile=forged, goal_bytes=data, verifier=VERIFIER)
    with pytest.raises(ValueError, match="needs goal_bytes and verifier"):
        build_profile.run_verify_edit(built[0], ledger_file=tmp_path / "ledger.json", facts_path=built[3],
                                      output_dir=tmp_path / "ve", repository_root=ROOT, parent_profile=c1)


def test_sg_partition_passes_only_when_a_phase_compares_two_evidenced_links():
    # Attack findings: one scored link per phase, or state strings shorter than the bound linkIndex, reported
    # consistent/pass with a matched CTL row; locked MAP lanes without a tl/linkIndex connection counted as evidenced.
    root = _x4()
    links = {ids.connection_key(row): int(row.get("linkIndex")) for row in root.findall("connection") if row.get("tl")}
    pairs = [("W_in_0", "S_out_0"), ("W_in_1", "E_out_1"), ("W_in_2", "N_out_2")]
    bindings = [{"node_id": "M", "connection_id": str(n), "signal_group": "7", "map_lanes": [f"m{n}", f"x{n}"],
                 "lane_pair": list(pair)} for n, pair in enumerate(pairs)]
    width = max(len(phase.get("state")) for phase in root.find("tlLogic").findall("phase"))

    def phases(*columns: str) -> ET.Element:
        net = copy.deepcopy(root)
        logic = net.find("tlLogic")
        for phase in logic.findall("phase"):
            logic.remove(phase)
        for letters in columns:
            state = ["r"] * width
            for pair, letter in zip(pairs, letters):
                state[links[">".join(pair)]] = letter
            ET.SubElement(logic, "phase", duration="10", state="".join(state))
        return net

    one = signal_partition.check(phases("GOO", "rOO", "yss"), bindings)
    assert (one["status"], one["groups"][0]["status"], one["rows"]) == ("unscored", "unscored", {})
    assert one["groups"][0]["compared_phases"] == 0 and one["evidenced_bindings"] == 3
    short = phases("GGr")
    for phase in short.find("tlLogic").findall("phase"):
        phase.set("state", phase.get("state")[:min(links[">".join(pair)] for pair in pairs)])
    assert signal_partition.check(short, bindings)["status"] == "unscored"
    compared = signal_partition.check(phases("GOO", "GGr"), bindings)
    assert compared["status"] == "inconsistent" and compared["groups"][0]["compared_phases"] == 1
    assert signal_partition.check(phases("GOO", "GGO"), bindings)["status"] == "pass"
    untied = phases("GGr")
    for row in untied.findall("connection"):
        row.attrib.pop("tl", None)
        row.attrib.pop("linkIndex", None)
    result = signal_partition.check(untied, bindings)
    assert (result["status"], result["evidenced_bindings"], result["locked_bindings"]) == ("not_applicable", 0, 3)


def test_l2_is_unverified_when_an_expected_movement_on_the_op_targets_fails_on_both_nets(tmp_path, monkeypatch):
    # Attack finding: an expected movement that fails on parent and candidate alike (the passenger smoke skipped every
    # non-passenger movement: 11 of 83 in K2) is never compared, so a candidate breaking it dynamically passed L2.
    from astra_sumo.core import movement_routeability

    clean = _x4()
    faulted = copy.deepcopy(clean)
    _lane(faulted, "W_in_1").set("speed", "10")
    built = _goal(tmp_path, faulted, reference=clean)
    speed = {"id": "op", "operation": "set_speed", "params": {"lane_id": "W_in_1", "speed_m_s": 13.89}}
    repaired = copy.deepcopy(faulted)
    _lane(repaired, "W_in_1").set("speed", "13.89")
    blind = _judge(tmp_path, built, repaired, operation=speed, seeds=True, l2=True, broken=(("W_in_1", "E_out_1"),),
                   everywhere=True)
    assert blind["gate"]["screening"] == "l2_unverified" and blind["gate"]["L2"]["result"] == "unverified"
    assert blind["gate"]["L2"]["unprobed"] == ["J0:movement:W_in_1>E_out_1"]
    elsewhere = _judge(tmp_path, built, repaired, operation=speed, seeds=True, l2=True,
                       broken=(("N_in_0", "W_out_0"),), everywhere=True)
    assert elsewhere["gate"]["screening"] == "improved"
    assert next(row for row in elsewhere["gate"]["L2"]["junctions"] if row["junction"] == "J0")["unprobed"] == [
        "movement:N_in_0>W_out_0"]
    # The smoke drives every movement with a class its turn permissions admit (bus, bicycle, ...), not passengers only.
    seen = {}

    def smoke(**kwargs):
        seen.update(kwargs)
        raise RuntimeError("stop")

    monkeypatch.setattr(movement_routeability, "run_all_turn_movement_smoke", smoke)
    with pytest.raises(RuntimeError, match="stop"):
        movement_guard.junction_smoke(built[4], "J0", [], tmp_path / "smoke", "sumo")
    assert seen["vehicle_class_per_movement"] is True and "permitted_movements_only" not in seen


def _movement_manifest(net: Path, out: Path) -> Path:
    """A minimal supported (Hamburg schema) movement manifest declaring every direct X4 movement of ``net``."""
    root = ET.parse(net).getroot()
    required = [{"sumo_connection": [row.get("from"), int(row.get("fromLane")), row.get("to"), int(row.get("toLane"))]}
                for row in root.findall("connection") if not row.get("from").startswith(":")]
    out.write_text(json.dumps({"schema": movement_guard.MANIFEST_SCHEMAS[0], "artifacts": {"network": {
        "path": str(net), "sha256": file_sha(net)}}, "counts": {"official_vehicle_movements": len(required)},
        "official_connection_audit": {"required": required, "ambiguous": []}}), encoding="utf-8")
    return out


def test_a_goal_movement_manifest_runs_the_l2_probes_on_manifests_bound_to_each_net(tmp_path, monkeypatch):
    # Attack finding: no goal, CLI or K2 path supplied manifests, so the probe branch was unreachable, and when probes
    # ran an expected movement absent from (or not tested by) the probes was no failure.
    from astra_sumo.core import movement_routeability

    clean = _x4()
    faulted = copy.deepcopy(clean)
    _lane(faulted, "W_in_1").set("speed", "10")
    source_net = _write(faulted, tmp_path / "source.net.xml")
    manifest = _movement_manifest(source_net, tmp_path / "manifest.json")
    with pytest.raises(ValueError, match="movement manifest"):
        goal.create_build_goal(ROOT, net=_write(clean, tmp_path / "other.net.xml"), reference_net=source_net,
                               target_date="2026-10-25", site_id="x4", mutable_edge_ids=["W_in"], seeds=[42],
                               movement_manifest=manifest)
    built = _goal(tmp_path, faulted, reference=clean, movement_manifest=manifest)
    assert built[0].guards.movement_manifest.sha256 == file_sha(manifest)
    omit: set[tuple[str, str]] = set()

    def probes(*, candidate_manifest, output_dir, sumo_binary):
        document = json.loads(Path(candidate_manifest).read_text(encoding="utf-8"))
        network = Path(document["artifacts"]["network"]["path"])
        assert file_sha(network) == document["artifacts"]["network"]["sha256"]
        Path(output_dir).mkdir(parents=True)
        report = Path(output_dir) / "summary.json"
        report.write_text("{}", encoding="utf-8")
        candidate = "candidate" in Path(output_dir).name
        rows = [{"sumo_connection": row["sumo_connection"], "status": "pass"}
                for row in document["official_connection_audit"]["required"]
                if not (candidate and (f"{row['sumo_connection'][0]}_{row['sumo_connection'][1]}",
                                       f"{row['sumo_connection'][2]}_{row['sumo_connection'][3]}") in omit)]
        return {"source_immutable": True, "probe_count": len(rows), "not_tested": [], "report_file": str(report),
                "results": rows}

    monkeypatch.setattr(movement_routeability, "run_candidate_movement_probes", probes)
    speed = {"id": "op", "operation": "set_speed", "params": {"lane_id": "W_in_1", "speed_m_s": 13.89}}
    repaired = copy.deepcopy(faulted)
    _lane(repaired, "W_in_1").set("speed", "13.89")
    good = _judge(tmp_path, built, repaired, operation=speed, seeds=True, l2=True)
    assert good["gate"]["screening"] == "improved" and good["gate"]["L2"]["method"] == "movement_probes"
    record = json.loads(Path(good["inputs"]["movement_guard"]["path"]).read_text(encoding="utf-8"))
    assert record["manifest_source"]["sha256"] == file_sha(manifest)
    for role in ("parent", "candidate"):
        derived = json.loads(Path(record["manifests"][role]["path"]).read_text(encoding="utf-8"))
        assert derived["artifacts"]["network"]["sha256"] == record[role]["sha256"]
    omit.add(("W_in_1", "E_out_1"))
    missing = _judge(tmp_path, built, repaired, operation=speed, seeds=True, l2=True)
    assert missing["gate"]["screening"] == "l2_new_failures"
    assert missing["gate"]["L2"]["new_failures"] == ["*:movement:W_in_1>E_out_1"]
    # A smoke record where the goal requires the probes does not bind the candidate.
    scope = {"parent_sha256": "p", "candidate_sha256": "c", "junctions": [], "expected": {"parent": {},
                                                                                         "candidate": {}}}
    smoke_record = {"schema": build_gate.MOVEMENT_GUARD_SCHEMA, "method": "all_turn_smoke", "parent": {"sha256": "p"},
                    "candidate": {"sha256": "c"}, "junctions": [], "expected": scope["expected"], "lane_mapping": {},
                    "nets_unchanged": True}
    assert build_gate.l2_rule(smoke_record, **scope, method="movement_probes")["result"] == "unverified"
    # A lane-count remap renames the declared movements; one on a removed lane becomes not tested.
    derived = movement_guard.derive_manifest(manifest, source_net, {"E_out": {"0": "1", "1": "2"}},
                                             tmp_path / "derived")
    audit = json.loads(derived.read_text(encoding="utf-8"))["official_connection_audit"]
    connections = [row["sumo_connection"] for row in audit["required"]]
    assert ["W_in", 1, "E_out", 2] in connections and ["S_in", 0, "E_out", 1] in connections
    assert [row["sumo_connection"] for row in audit["ambiguous"]] == [["N_in", 2, "E_out", 2]]
    assert build_gate.l2_failing({"failing": [], "tested": [], "not_tested": [["a_0", "b_0"]], "run_ok": True},
                                 [["a_0", "b_0"]]) == {"movement:a_0>b_0"}


def _speed_goal_without_movement_facts(tmp_path: Path, **kwargs):
    """X4 with a W_in_1 speed fault, a goal whose frozen facts are speed facts only (no scored movement_inventory
    fact, so L2 expects no movement anywhere), the honest repair and its op."""
    clean = _x4()
    faulted = copy.deepcopy(clean)
    _lane(faulted, "W_in_1").set("speed", "10")
    facts = _facts_for_kinds(tmp_path, clean, ("speed",), ("W_in",))
    built = _goal(tmp_path, faulted, reference=clean, facts=facts, **kwargs)
    assert {fact["kind"] for fact in json.loads(built[2])["facts"]} == {"speed"}
    repaired = copy.deepcopy(faulted)
    _lane(repaired, "W_in_1").set("speed", "13.89")
    return built, repaired, {"id": "op", "operation": "set_speed", "params": {"lane_id": "W_in_1", "speed_m_s": 13.89}}


def test_failed_l2_probe_commands_never_permit_acceptance_without_movement_facts(tmp_path, monkeypatch):
    # Review finding (P1): probe_summary set run_ok from source_immutable alone. With no scored movement_inventory
    # fact, a review_required probe whose SUMO command exited 1 on parent and candidate alike cancelled out and L2
    # passed (no_new_failures). Any execution failure on either net makes that run not ok: L2 is unverified.
    from astra_sumo.core import movement_routeability

    faulted = _x4()
    _lane(faulted, "W_in_1").set("speed", "10")
    manifest = _movement_manifest(_write(faulted, tmp_path / "manifest-source.net.xml"), tmp_path / "manifest.json")
    built, repaired, speed = _speed_goal_without_movement_facts(tmp_path, movement_manifest=manifest)
    failure = {"parent": None, "candidate": None}
    commands = {"returncode": {"status": "fail", "returncode": 1, "error": "command exited with return code 1"},
                "timeout": {"status": "timeout", "returncode": None, "error": "command timed out after 120 seconds"}}

    def probes(*, candidate_manifest, output_dir, sumo_binary):
        document = json.loads(Path(candidate_manifest).read_text(encoding="utf-8"))
        Path(output_dir).mkdir(parents=True)
        report = Path(output_dir) / "summary.json"
        report.write_text("{}", encoding="utf-8")
        mode = failure["candidate" if "candidate" in Path(output_dir).name else "parent"]
        rows = []
        for index, row in enumerate(document["official_connection_audit"]["required"]):
            ran = {"sumo_connection": row["sumo_connection"], "status": "pass", "parse_error": None,
                   "command": {"status": "pass", "returncode": 0, "error": ""}, "summary": {"arrived": "1"}}
            if index == 0 and mode in commands:
                ran.update(status="review_required", command=commands[mode], summary={})
            elif index == 0 and mode == "unreadable":
                ran.update(status="review_required", summary={}, parse_error="no element found: line 1, column 0")
            rows.append(ran)
        return {"source_immutable": True, "probe_count": len(rows), "not_tested": [], "report_file": str(report),
                "results": rows}

    monkeypatch.setattr(movement_routeability, "run_candidate_movement_probes", probes)
    good = _judge(tmp_path, built, repaired, operation=speed, seeds=True, l2=True)
    assert good["gate"]["screening"] == "improved" and good["gate"]["L2"]["method"] == "movement_probes"
    record = json.loads(Path(good["inputs"]["movement_guard"]["path"]).read_text(encoding="utf-8"))
    assert record["expected"] == {"parent": {}, "candidate": {}}
    # Codex's reproduction first: the same probe exits 1 on both nets.
    for parent_mode, candidate_mode in (("returncode", "returncode"), ("timeout", "timeout"),
                                        ("unreadable", "unreadable"), (None, "returncode"), ("returncode", None)):
        failure.update(parent=parent_mode, candidate=candidate_mode)
        judged = _judge(tmp_path, built, repaired, operation=speed, seeds=True, l2=True)
        label = (parent_mode, candidate_mode)
        assert judged["gate"]["screening"] == "l2_unverified", label
        assert (judged["gate"]["L2"]["status"], judged["gate"]["L2"]["result"]) == ("blocked", "unverified"), label
        record = json.loads(Path(judged["inputs"]["movement_guard"]["path"]).read_text(encoding="utf-8"))
        for role, mode in (("parent", parent_mode), ("candidate", candidate_mode)):
            run = record["runs"][role][build_gate.NETWORK_RUN]
            assert run["run_ok"] is (mode is None) and bool(run["execution_errors"]) is (mode is not None), label
        assert judged["gate"]["L2"]["junctions"][0]["unclean_runs"] == [
            role for role, mode in (("parent", parent_mode), ("candidate", candidate_mode)) if mode], label
    # A probe the core never ran (no unique internal chain) is a movement failure, not an execution error.
    assert movement_guard.probe_execution_error({"status": "review_required",
                                                 "reason": "no_unique_structurally_valid_internal_chain"}) is None
    assert movement_guard.probe_execution_error({"status": "pass", "command": {"status": "pass", "returncode": 0},
                                                 "summary": {"arrived": "1"}, "parse_error": None}) is None


def _core_smoke(failure: dict):
    """``run_all_turn_movement_smoke`` without SUMO, in the real report shape: every direct movement of the junction
    is driven and arrives, unless ``failure`` names an execution failure for the net's role ("returncode",
    "timeout", or "tripinfo" for a run whose tripinfo output is missing)."""
    def smoke(*, net_file, target_junction_id, output_dir, **_):
        root = ET.parse(net_file).getroot()
        ends = {edge.get("id"): (edge.get("from"), edge.get("to")) for edge in root.findall("edge")
                if edge.get("function") != "internal"}
        movements = [{"from": row.get("from"), "from_lane": row.get("fromLane"), "to": row.get("to"),
                      "to_lane": row.get("toLane"), "vehicle_class": "passenger"} for row in root.findall("connection")
                     if ends.get(row.get("from"), (None, None))[1] == target_junction_id
                     and ends.get(row.get("to"), (None, None))[0] == target_junction_id]
        ids = [f"movement_{index:02d}" for index in range(len(movements))]
        mode = failure["candidate" if Path(net_file).name == "candidate.net.xml" else "parent"]
        commands = {"returncode": {"status": "fail", "returncode": 1}, "timeout": {"status": "timeout",
                                                                                  "returncode": None}}
        command = commands.get(mode, {"status": "pass", "returncode": 0})
        Path(output_dir).mkdir(parents=True)
        report = Path(output_dir) / "all-turns.report.json"
        report.write_text("{}", encoding="utf-8")
        arrived = [] if mode else ids
        events = {key: 0 for key in movement_guard.EVENT_KEYS}
        checks = {"sumo_command": mode not in commands, "source_immutable": True,
                  "all_expected_vehicles_arrived": not mode, "runtime_outputs": not mode}
        return {"status": "fail" if mode else "pass", "movements": movements, "expected_vehicle_ids": ids,
                "arrived_vehicle_ids": arrived, "vehicle_class_per_movement": True, "skipped_movements": [],
                "checks": checks, "command_result": command, "report_file": str(report),
                "inspection": {"status": "fail" if mode == "tripinfo" else "pass",
                               "summary": {**events, "arrived": len(arrived)}}}
    return smoke


def test_failed_all_turn_smoke_runs_never_permit_acceptance_without_movement_facts(tmp_path, monkeypatch):
    # Review finding (P1), the all-turn smoke path: with no scored movement_inventory fact, a smoke whose tripinfo
    # output was missing (nothing read as arrived on either net) cancelled out and passed, and a smoke command that
    # failed only on the candidate read as new movement failures. Any execution failure on either net: unverified.
    from astra_sumo.core import movement_routeability

    built, repaired, speed = _speed_goal_without_movement_facts(tmp_path)
    failure = {"parent": None, "candidate": None}
    monkeypatch.setattr(movement_routeability, "run_all_turn_movement_smoke", _core_smoke(failure))

    def smoke(net, junction, pairs, out):
        return movement_guard.junction_smoke(net, junction, pairs, out, "sumo")

    good = _judge(tmp_path, built, repaired, operation=speed, seeds=True, l2=True, smoke=smoke)
    assert good["gate"]["screening"] == "improved" and good["gate"]["L2"]["method"] == "all_turn_smoke"
    record = json.loads(Path(good["inputs"]["movement_guard"]["path"]).read_text(encoding="utf-8"))
    assert record["expected"] == {"parent": {}, "candidate": {}} and record["junctions"] == ["J0", "W"]
    for parent_mode, candidate_mode in (("returncode", "returncode"), ("tripinfo", "tripinfo"),
                                        ("timeout", "timeout"), (None, "returncode"), (None, "tripinfo"),
                                        ("tripinfo", None)):
        failure.update(parent=parent_mode, candidate=candidate_mode)
        judged = _judge(tmp_path, built, repaired, operation=speed, seeds=True, l2=True, smoke=smoke)
        label = (parent_mode, candidate_mode)
        assert judged["gate"]["screening"] == "l2_unverified", label
        assert (judged["gate"]["L2"]["result"], judged["gate"]["L2"]["new_failures"]) == ("unverified", []), label
        record = json.loads(Path(judged["inputs"]["movement_guard"]["path"]).read_text(encoding="utf-8"))
        for role, mode in (("parent", parent_mode), ("candidate", candidate_mode)):
            assert all(run["run_ok"] is (mode is None) and bool(run["execution_errors"]) is (mode is not None)
                       for run in record["runs"][role].values()), label


# ---- views, determinism and the CLI ------------------------------------------------------------------------------

def test_ring1_source_profile_is_deterministic_capped_and_clean(tmp_path):
    created, facts, summary = goal.create_build_goal(ROOT, net=RING1, reference_net=RING1, target_date="2026-10-22",
                                                     site_id="ring1-nominal", mutable_edge_ids=["-1189704921"])
    assert summary["scored"] == 6353 and summary["source_V"] == [0, 0, 0, 0, 0.0]
    assert created.source.sha256.startswith("cc0453749470") and created.source.lane_elements == 2219
    path, _, _ = goal.write_build_goal(created, facts, tmp_path / "store")
    loaded, data, facts_bytes, facts_path = goal.load_build_goal(path)
    profiles = []
    for name in ("a", "b"):
        build_profile.write_build_profile(loaded, data, facts_bytes, facts_path=facts_path, repository_root=ROOT,
                                          out_dir=tmp_path / name, verifier=VERIFIER)
        profile = json.loads((tmp_path / name / "profile.full.json").read_text(encoding="utf-8"))
        profile.pop("timing")
        profiles.append(profile)
    assert profiles[0] == profiles[1] and profiles[0]["hotspots"] == []
    assert all((tmp_path / "a" / name).read_bytes() == (tmp_path / "b" / name).read_bytes()
               for name in ("profile.compact.json", "profile.compact.txt", "facts.csv"))
    assert profiles[0]["direction"]["finding_count"] == 0
    assert all(row["characters"] <= render.CAP_CHARACTERS for row in profiles[0]["compact"].values())


def test_build_compact_views_are_bounded_when_every_invariant_fails(tmp_path):
    # Review finding: with all 14 invariants failing and long lists the last budget still exceeded the cap, so no
    # profile was written; the last budget now keeps counts only.
    built = _goal(tmp_path, _x4())
    profile = _judge(tmp_path, built, _x4())
    witnesses = [f"kind{n}{'k' * 40}:{'w' * 60}{n}" for n in range(20)]
    profile["invariants"] = [{**check, "pass": False, "witness_ids": witnesses} for check in profile["invariants"]]
    # The former last budget (every id and list clipped) does not fit; the counts-only budget does.
    assert len(json.dumps(render.compact_build(profile, hotspots=0, rows=0, escalations=0, clip=True),
                          separators=(",", ":"))) > render.CAP_CHARACTERS
    with pytest.raises(ValueError, match="exceeds"):
        render.render_build_compact(profile, cap=1000)
    profile["gate"].update(measurability_lost=[f"lost-{'x' * 50}{n}" for n in range(200)],
                           regressed_facts=[f"regressed-{'y' * 50}{n}" for n in range(200)],
                           fixed_rows=[{"fact": f"f{'z' * 60}{n}", "parent": {"excess": 1.0},
                                        "candidate": {"status": "matched"}} for n in range(50)],
                           first_fail="x" * 5000)
    profile["op_text"] = "set_lane_count " + json.dumps({"connections": [[f"a{n}" * 8, f"b{n}" * 8]
                                                                          for n in range(40)]})
    compact, text = render.render_build_compact(profile)
    assert len(compact) <= render.CAP_CHARACTERS and len(text) <= render.CAP_CHARACTERS
    assert json.loads(compact)["invariants"]["INV-FACTS"] == {"n": 20} and "(counts only)" in text


def test_build_compact_views_stay_within_the_cap_with_many_long_hotspots(tmp_path):
    faulted = _x4("wrong-to-lane")
    profile = _profile(_goal(tmp_path, faulted))
    row = profile["hotspots"][1]["rows"][0]
    profile["hotspots"] = [{"id": f"H:GEO:edge:{'x' * 90}{index}", "class": "GEO", "anchor": "edge:x", "weight": 1.0,
                            "rows": [{**row, "id": f"lane:{'y' * 80}{index}_{n}"} for n in range(12)]}
                           for index in range(60)]
    profile["escalations"] = [{"id": f"H:COR:lane:{'z' * 70}{n}", "class": "COR",
                               "facts": [{"fact": f"f{n}", "status": build_profile.COR_ALL_FAIL_TEXT}] * 3}
                              for n in range(40)]
    compact, text = render.render_build_compact(profile)
    assert len(compact) <= render.CAP_CHARACTERS and len(text) <= render.CAP_CHARACTERS
    assert "+" in text and json.loads(compact)["kind"] == "build"


def _written(built, out: Path, **kwargs) -> Path:
    loaded, data, facts_bytes, facts_path, _ = built
    build_profile.write_build_profile(loaded, data, facts_bytes, facts_path=facts_path, repository_root=ROOT,
                                      out_dir=out, verifier=VERIFIER, **kwargs)
    return out / "profile.full.json"


def test_parent_profiles_from_another_goal_are_refused(tmp_path):
    built = _goal(tmp_path / "a", _x4())
    other = _goal(tmp_path / "b", _x4("wrong-turn"))
    parent = _written(other, tmp_path / "other-source")
    with pytest.raises(ValueError, match="another goal"):
        _profile(built, net=built[4], parent_profile=parent)
    with pytest.raises(ValueError, match="no parent"):
        _profile(built, parent_profile=_written(built, tmp_path / "a-source"))


def test_only_the_goal_source_or_an_accepted_hash_bound_candidate_is_a_parent(tmp_path):
    # Review finding (blocker): any same-goal profile, a raw net or a G0-rejected candidate included, was accepted as
    # parent and not hash-bound, so a G0 exploit could be laundered through one unjudged hop.
    clean = _x4()
    faulted = copy.deepcopy(clean)
    _lane(faulted, "E_out_2").set("speed", "10")
    _lane(faulted, "S_in_0").set("width", "3.5")
    built = _goal(tmp_path, faulted, reference=clean, mutable_edge_ids=["E_out", "S_in", "W_in"])
    source = _written(built, tmp_path / "source")
    speed = {"id": "op", "operation": "set_speed", "params": {"lane_id": "E_out_2", "speed_m_s": 13.89}}
    width = {"id": "op", "operation": "set_lane_width", "params": {"lane_id": "S_in_0", "width_m": 3.2}}
    first = copy.deepcopy(faulted)
    _lane(first, "E_out_2").set("speed", "13.89")
    # Without L2 the same step is never accepted, so it is never a parent (review finding P1-b).
    unprobed = _judge(tmp_path, built, first, operation=speed, seeds=True, parent_profile=source,
                      out=tmp_path / "c0")
    assert (unprobed["status"], unprobed["gate"]["screening"]) == ("measured", "l2_not_run")
    with pytest.raises(ValueError, match="was not accepted"):
        _judge(tmp_path, built, first, operation=speed, seeds=True, l2=True,
               parent_net=Path(unprobed["inputs"]["net"]["path"]), parent_profile=tmp_path / "c0" / "profile.full.json")
    step1 = _judge(tmp_path, built, first, operation=speed, seeds=True, l2=True, parent_profile=source,
                   out=tmp_path / "c1")
    assert (step1["status"], step1["gate"]["screening"]) == ("measured", "improved")
    assert step1["gate"]["L2"]["result"] == "no_new_failures" and step1["gate"]["L3"]["result"] == "subset_holds"
    assert step1["gate"]["L2"]["method"] == "all_turn_smoke" and step1["inputs"]["movement_guard"]["sha256"]
    assert step1["inputs"]["parent_profile"]["subject"] == "source"
    assert step1["inputs"]["parent_net"]["scorecard"].startswith("goal-create scorecard")
    second = copy.deepcopy(first)
    _lane(second, "S_in_0").set("width", "3.2")
    first_net = Path(step1["inputs"]["net"]["path"])
    step2 = _judge(tmp_path, built, second, operation=width, seeds=True, l2=True, parent_net=first_net,
                   parent_profile=tmp_path / "c1" / "profile.full.json", out=tmp_path / "c2")
    assert step2["V"]["cand"] == [0, 0, 0, 0, 0.0] and step2["inputs"]["parent_profile"]["depth"] == 1
    assert step2["gate"]["screening"] == "improved"
    assert step2["inputs"]["parent_net"]["scorecard"] == "rescored: the parent is not the goal source"
    # A raw net, never judged, cannot become a parent; neither can a G0-rejected candidate.
    raw = _written(built, tmp_path / "raw", net=_write(first, tmp_path / "raw.net.xml"))
    with pytest.raises(ValueError, match="raw_net profile is never a parent"):
        _judge(tmp_path, built, second, operation=width, parent_net=tmp_path / "raw.net.xml", parent_profile=raw)
    zigzag = copy.deepcopy(faulted)
    _lane(zigzag, "W_in_1").set("shape", " ".join(f"{-100 + 0.5 * n},{0.4 if n % 2 else 0.0}" for n in range(191)))
    rejected = _judge(tmp_path, built, zigzag, operation=speed, seeds=True, l2=True, out=tmp_path / "bad")
    assert rejected["gate"]["G0"] == "fail"
    with pytest.raises(ValueError, match="was not accepted"):
        _judge(tmp_path, built, second, operation=width, parent_net=Path(rejected["inputs"]["net"]["path"]),
               parent_profile=tmp_path / "bad" / "profile.full.json")
    # The chain is hash-bound: a grandparent edited after it was recorded, or a parent's changed net, is refused.
    c1 = tmp_path / "c1" / "profile.full.json"
    c1.write_bytes(c1.read_bytes() + b"\n")
    with pytest.raises(ValueError, match="does not match its recorded sha256"):
        _judge(tmp_path, built, second, operation=width, parent_net=Path(step2["inputs"]["net"]["path"]),
               parent_profile=tmp_path / "c2" / "profile.full.json")
    c1.write_bytes(c1.read_bytes()[:-1])
    guard = Path(step1["inputs"]["movement_guard"]["path"])
    guard.write_bytes(guard.read_bytes() + b"\n")
    with pytest.raises(ValueError, match="movement-guard record of parent"):
        _judge(tmp_path, built, second, operation=width, parent_net=first_net,
               parent_profile=tmp_path / "c1" / "profile.full.json")
    guard.write_bytes(guard.read_bytes()[:-1])
    first_net.write_bytes(first_net.read_bytes() + b"<!-- changed -->")
    with pytest.raises(ValueError, match="candidate net of parent"):
        _judge(tmp_path, built, second, operation=width, parent_net=first_net,
               parent_profile=tmp_path / "c1" / "profile.full.json")


def test_goal_ids_and_fact_references_do_not_depend_on_the_checkout_location(tmp_path):
    # Review finding: the reference table embedded the absolute reference-net path, so the same inputs gave another
    # goal_id and cas reference in another clone; lane artifacts were stored as absolute paths too.
    made = []
    for name in ("a", "b"):
        root = tmp_path / name / "clone"
        root.mkdir(parents=True)
        net, clean = _write(_x4("wrong-turn"), root / "source.net.xml"), _write(_x4(), root / "ref.net.xml")
        artifact = root / "e1.add.xml"
        artifact.write_bytes(b'<additional><inductionLoop id="z" lane="W_in_0" pos="5" period="60" file="x"/>'
                             b'</additional>')
        created, payloads, _ = goal.create_build_goal(root, net=net, reference_net=clean, target_date="2026-10-25",
                                                      site_id="x4", mutable_junction_ids=["J0"], seeds=[42],
                                                      lane_artifacts=[artifact], scratch_dir=tmp_path / name)
        made.append((created, payloads))
    (first, first_payloads), (second, second_payloads) = made
    assert first.goal_id == second.goal_id and first.reference.facts == second.reference.facts
    assert first.to_json_bytes() == second.to_json_bytes() and set(first_payloads) == set(second_payloads)
    assert first.guards.lane_artifacts[0].path == "e1.add.xml" and first.reference.reference_net.path == "ref.net.xml"
    assert [row.status for row in first.guards.routeability.trips] == ["generated"]


def test_goal_store_writes_are_atomic_and_repair_a_damaged_entry(tmp_path):
    # Review finding: the fact table was written non-atomically and skipped whenever the file existed, so a
    # truncated entry was never repaired and every later load was refused.
    built = _goal(tmp_path, _x4())
    loaded, _, facts_bytes, facts_path, _ = built
    facts_path.write_bytes(facts_bytes[:100])
    created, payloads, _ = goal.create_build_goal(ROOT, net=built[4], reference_net=tmp_path / "reference.net.xml",
                                                  target_date="2026-10-25", site_id="x4",
                                                  mutable_junction_ids=["J0"], mutable_edge_ids=["W_in"], seeds=[42])
    assert created.goal_id == loaded.goal_id
    path, written, cas = goal.write_build_goal(created, payloads, tmp_path / "store")
    assert (written, cas.read_bytes()) == ("unchanged", facts_bytes)
    assert goal.load_build_goal(path)[2] == facts_bytes
    assert not list((tmp_path / "store" / "cas" / "sha256").glob(".torii-*"))
    with pytest.raises(ValueError, match="missing content"):
        goal.write_build_goal(created, {created.reference.facts: facts_bytes}, tmp_path / "store")


def test_the_lane_count_remap_stays_in_the_given_scratch_directory(tmp_path, monkeypatch):
    # Review finding: the remap wrote candidate-facts.json into the system temp directory.
    from astra_sumo.core import network_edit_operations

    seen = []

    def remap(facts, plan, output):
        seen.append(Path(output))
        Path(output).write_bytes(b"{}")

    monkeypatch.setattr(network_edit_operations, "_remap_lane_count_facts", remap)
    plan = tmp_path / "plan.json"
    plan.write_bytes(b"{}")
    _, record = build_profile._remapped_facts({"plan_file": str(plan)}, tmp_path / "facts.json", tmp_path / "scratch")
    assert seen[0].parent.parent == tmp_path / "scratch" and not seen[0].exists()
    assert record["basis"] == "declared_remap" and record["verified"] is False


def test_src_comes_from_source_dates_only_and_shared_helpers_are_single():
    # Review finding: speculative code read an undeclared mapping_diagnostics.source_values key.
    fact = type("Fact", (), {"source_ids": [], "mapping_diagnostics": {"source_values": [{"value": 1, "sigma": 0},
                                                                                         {"value": 9, "sigma": 0}]}})
    assert build_profile.source_conflict_reason(fact, {}, None) is None
    assert direction_check.connection_key is ids.connection_key
    assert invariants.TARGET_CLASSES is build_gate.TARGET_CLASSES


def _cli():
    spec = importlib.util.spec_from_file_location("astra_twin_cli_build",
                                                  ROOT / "plugins/astra-sumo/scripts/astra_twin.py")
    cli = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cli)
    return cli


def _run(cli, argv: list[str]) -> tuple[int, dict]:
    stream = io.StringIO()
    with redirect_stdout(stream):
        code = cli.main(argv)
    return code, json.loads(stream.getvalue())


def test_cli_goal_create_build_and_offline_profiles(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "dont_write_bytecode", sys.dont_write_bytecode)
    cli = _cli()
    source, clean = _write(_x4("wrong-to-lane"), tmp_path / "source.net.xml"), _write(_x4(), tmp_path / "ref.net.xml")
    create = ["goal-create", "--capability", "build", "--store", str(tmp_path / "store"), "--net", str(source),
              "--reference-net", str(clean), "--target-date", "2026-10-25", "--site-id", "x4",
              "--mutable-junction", "J0", "--seed", "42", "--json"]
    code, created = _run(cli, create)
    assert (code, created["status"], created["write"], created["scored"]) == (0, "pass", "created", 164)
    assert _run(cli, create)[1]["write"] == "unchanged"
    goal_file = created["goal"]
    code, source_profile = _run(cli, ["profile", "build", "--goal", goal_file, "--out", str(tmp_path / "p1"),
                                      "--json"])
    assert (code, source_profile["status"], source_profile["screening"]) == (0, "measured", "source_profile")
    assert source_profile["hotspots"][0] == "H:INT:junction:J0" and source_profile["V"] == [1, 1, 0, 0, 0.0]
    code, raw = _run(cli, ["profile", "build", "--goal", goal_file, "--candidate-net", str(clean), "--out",
                           str(tmp_path / "p2"), "--json"])
    assert (code, raw["screening"], raw["V"], raw["hotspots"]) == (0, "not_judged", [0, 0, 0, 0, 0.0], [])
    code, again = _run(cli, ["profile", "build", "--goal", goal_file, "--out", str(tmp_path / "p1"), "--json"])
    assert (code, again["status"], again["error_type"]) == (3, "blocked", "FileExistsError")
    # Review finding: the Calibrate usage error is argparse's again (exit 2), as at 6ccaac5.
    with pytest.raises(SystemExit) as usage:
        _run(cli, ["goal-create", "--capability", "calibrate", "--store", str(tmp_path / "s"), "--json"])
    assert usage.value.code == 2
    code, ignored = _run(cli, ["profile", "build", "--goal", goal_file, "--netconvert", "x", "--out",
                               str(tmp_path / "p3"), "--json"])
    assert (code, ignored["status"]) == (3, "blocked") and "only apply with --ledger" in ignored["reason"]
    code, both = _run(cli, ["profile", "build", "--goal", goal_file, "--ledger", "l.json", "--no-routeability",
                            "--routeability-seed", "17", "--out", str(tmp_path / "p4"), "--json"])
    assert (code, both["status"]) == (3, "blocked") and "contradict" in both["reason"]
    assert "no SUMO runs in M1" not in cli.__doc__
    code, refused = _run(cli, ["goal-create", "--capability", "build", "--store", str(tmp_path / "s"), "--net",
                               str(source), "--json"])
    assert (code, refused["status"]) == (3, "blocked") and "--facts or --reference-net" in refused["reason"]
    # A movement manifest reaches the goal (and so L2's probes) only when it is bound to the source net.
    for net, expected in ((clean, 3), (source, 0)):
        manifest = _movement_manifest(net, tmp_path / f"{net.stem}.manifest.json")
        code, made = _run(cli, [*create[:4], str(tmp_path / f"store-{net.stem}"), *create[5:-1],
                                "--movement-manifest", str(manifest), "--json"])
        assert code == expected, made
    frozen = json.loads(Path(made["goal"]).read_text(encoding="utf-8"))["guards"]["movement_manifest"]
    assert frozen["sha256"] == file_sha(manifest)


@pytest.mark.skipif(not shutil.which("netconvert") or not shutil.which("sumo"), reason="SUMO binaries required")
def test_cli_judges_an_inverse_typed_op_through_verify_edit(tmp_path, monkeypatch):
    from astra_sumo.core.network_edit_applicability import verify_network_edit
    from test_network_edit_operations import _source

    monkeypatch.setattr(sys, "dont_write_bytecode", sys.dont_write_bytecode)
    cli = _cli()
    clean = _source(tmp_path, "move_stop_line")

    def ledger(name: str, source: Path, end_offset: float) -> Path:
        path = tmp_path / f"{name}.json"
        path.write_bytes(json.dumps({"source_net_file": str(source), "operations": [
            {"id": name, "operation": "move_stop_line",
             "params": {"lane_id": "middle_0", "end_offset_m": end_offset}}]}).encode("utf-8"))
        return path

    fault = verify_network_edit(ledger_file=ledger("fault", clean, 2), output_dir=tmp_path / "fault",
                                netconvert_binary=shutil.which("netconvert"), routeability_seeds=())
    faulted = tmp_path / "faulted.net.xml"
    shutil.copyfile(fault["candidate_net_file"], faulted)
    code, created = _run(cli, ["goal-create", "--capability", "build", "--store", str(tmp_path / "store"), "--net",
                               str(faulted), "--reference-net", str(clean), "--target-date", "2026-10-25",
                               "--mutable-edge", "middle", "--seed", "42", "--json"])
    assert code == 0 and created["source_V"] == [0, 1, 1, 0, 4.0]
    code, source = _run(cli, ["profile", "build", "--goal", created["goal"], "--out", str(tmp_path / "p0"), "--json"])
    assert source["hotspots"] == ["H:CTL:lane:middle_0", "H:GEO:edge:middle"]
    full = json.loads((tmp_path / "p0" / "profile.full.json").read_text(encoding="utf-8"))
    suggested = full["hotspots"][0]["rows"][0]["suggested_ops"][0]
    assert suggested["params"] == {"lane_id": "middle_0", "end_offset_m": 0.0}
    # One routeability seed (100 vehicles): verify-edit's source audit must run exactly the goal's frozen trips.
    code, judged = _run(cli, ["profile", "build", "--goal", created["goal"], "--ledger",
                              str(ledger("inverse", faulted, suggested["params"]["end_offset_m"])),
                              "--netconvert", shutil.which("netconvert"), "--routeability-seed", "42",
                              "--parent-profile", str(tmp_path / "p0" / "profile.full.json"),
                              "--out", str(tmp_path / "p1"), "--json"])
    # L2 (the all-turn smoke on the touched junctions) and L3 on the frozen trips both hold, so it is accepted.
    assert (code, judged["status"], judged["screening"]) == (0, "measured", "improved")
    assert (judged["L2"], judged["L3"]) == ("no_new_failures", "subset_holds")
    assert judged["V"] == [0, 0, 0, 0, 0.0] and judged["V_parent"] == [0, 1, 1, 0, 4.0]
    assert all(judged["invariants"].values()) and (tmp_path / "p1.verify-edit" / "edit-verification.json").is_file()
    assert (tmp_path / "p1.movement-guard" / movement_guard.RECORD).is_file()
    full = json.loads((tmp_path / "p1" / "profile.full.json").read_text(encoding="utf-8"))
    assert full["delta"]["fixed"] == ["lane:middle_0:centerline", "lane:middle_0:stop_line"]
    assert full["delta"]["hotspots_gone"] == ["H:CTL:lane:middle_0", "H:GEO:edge:middle"]
    assert full["gate"]["outside_fact_changes"] == []
    (seed,) = full["gate"]["L3"]["seeds"]
    assert full["gate"]["L3"]["result"] == "subset_holds" and seed["trips"]["source"] == seed["trips"]["goal"]
    assert seed["trips"]["candidate"] == seed["trips"]["goal"] is not None
    report = json.loads((tmp_path / "p1.verify-edit" / "edit-verification.json").read_text(encoding="utf-8"))
    assert report["source_routeability"]["od_policy"]["mode"] == "frozen_requests"
    l2 = full["gate"]["L2"]
    assert l2["method"] == "all_turn_smoke" and l2["junctions"] and not l2["new_failures"]
    assert full["inputs"]["parent_net"]["scorecard"].startswith("goal-create scorecard")
