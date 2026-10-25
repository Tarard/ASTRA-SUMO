"""Build profile (M3): per-kind acceptance, goal lint, V materiality, G0 exploits, the L3 hard rule and the views."""

from __future__ import annotations

import copy
import hashlib
import importlib.util
import io
import json
import shutil
import sys
import xml.etree.ElementTree as ET
from contextlib import redirect_stdout
from pathlib import Path

import pytest
from pydantic import ValidationError

from astra_sumo.core.network_edit_operations import check_lane_artifact_positions
from astra_sumo.core.reference_net_facts import build_reference_net_facts
from astra_sumo.corridor.enums import TrafficSide
from astra_sumo.corridor.synthetic_networks import apply_synthetic_mutation, build_synthetic_fixture
from astra_sumo.twin import build_gate, build_profile, direction_check, goal, ids, invariants, render

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


def _judge(tmp_path: Path, built, candidate: ET.Element, *, operation: dict | None = None,
           artifacts: list[Path] | None = None, affected: tuple[str, ...] = (), parent_net: Path | None = None,
           parent_profile: Path | None = None, out: Path | None = None, seeds: bool = False,
           structural: int = 0) -> dict:
    """Profile ``candidate`` through a fabricated verify-edit report whose construction checks all pass, so only the
    twin's own G0, V and L3 decide (no SUMO). ``seeds`` adds clean routeability audits on the goal's frozen trips;
    ``out`` writes the profile directory (``profile.full.json``) instead of returning it in memory."""
    loaded, data, facts_bytes, facts_path, source = built
    parent = parent_net or source
    work = tmp_path / f"judge-{len(list(tmp_path.glob('judge-*')))}"
    work.mkdir()
    net = _write(candidate, work / "candidate.net.xml")
    operation = operation or {"id": "op", "operation": "set_edge_shape",
                              "params": {"edge_id": "W_in", "shape": [[-100, 0], [-5, 0]]}}
    ledger = work / "ledger.json"
    ledger.write_bytes(json.dumps({"source_net_file": str(parent), "operations": [operation]}).encode("utf-8"))
    plan = work / "plan.json"
    plan.write_bytes(json.dumps({"mutable_junction_ids": ["J0", "W"], "mutable_edge_ids": ["W_in"]}).encode("utf-8"))
    scorecard = work / "source-scorecard.json"
    scorecard.write_bytes(json.dumps({"inputs": {str(facts_path): hashlib.sha256(facts_bytes).hexdigest()}}).encode())
    lane_artifacts = {"status": "not_applicable", "affected_lane_ids": list(affected)}
    if artifacts:
        lane_artifacts.update(check_lane_artifact_positions(candidate, artifacts, affected))
    sha = hashlib.sha256(net.read_bytes()).hexdigest()
    trips = [row for row in loaded.guards.routeability.trips if row.sha256] if seeds else []
    report = {"status": "measured", "candidate_net_file": str(net), "candidate_sha256": sha,
              "source": {"path": str(parent), "sha256": hashlib.sha256(parent.read_bytes()).hexdigest()},
              "ledger": {"path": str(ledger), "sha256": hashlib.sha256(ledger.read_bytes()).hexdigest()},
              "plan_file": str(plan), "source_immutable": True, "source_scorecard": str(scorecard),
              "outside_fact_changes": [], "routeability_seeds": [row.seed for row in trips],
              "routeability_by_seed": [{"seed": row.seed, "source_routeability": _passing_audit(row.sha256, frozen=False),
                                        "candidate_routeability": _passing_audit(row.sha256, frozen=True)}
                                       for row in trips],
              "construction": {"structural_failure_count": structural, "sumo_load": {"status": "pass"},
                               "source_immutable": True, "new_protected_green_conflicts": [],
                               "lane_coordinate_artifacts": lane_artifacts}}
    (work / "edit-verification.json").write_bytes(json.dumps(report).encode("utf-8"))
    if out is not None:
        return build_profile.write_build_profile(loaded, data, facts_bytes, facts_path=facts_path, repository_root=ROOT,
                                                 out_dir=out, verifier=VERIFIER, verify_edit=work,
                                                 parent_profile=parent_profile)
    return _profile(built, verify_edit=work, parent_profile=parent_profile)


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
    assert "heading:lane:W_in_1" in _failed(_judge(tmp_path, built, zigzag))["INV-SHAPE"]
    # The existing bend itself, re-drawn with one more collinear vertex, is excused by the parent's own bend.
    redrawn = copy.deepcopy(bent)
    _lane(redrawn, "W_in_1").set("shape", "-75.0,-43.30127 -50.0,0.0 -20.0,0.0 -5.0,0.0")
    assert "INV-SHAPE" not in _failed(_judge(tmp_path, built, redrawn))


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
    moved = copy.deepcopy(clean)
    moved.find("location").set("convBoundary", "-120,-100,100,100")
    assert "INV-FRAME" not in _failed(_judge(tmp_path, built, moved))


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
    assert max(m for _, m in large) > max(m for _, m in small) + invariants.MEASURE_TOLERANCE_M
    built = _goal(tmp_path, gapped(0.5), reference=gapped(0.5))
    failed = _failed(_judge(tmp_path, built, gapped(2.0), structural=len(large)))
    assert any(witness.startswith("worse:") for witness in failed["INV-MATERIALIZE"])


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
    assert build_gate.screening({"pass": True}, {"verdict": "improved"}, traded)[0] == "dynamic_hard"


def test_l3_dirty_sources_pass_only_identified_subsets_and_never_unidentified_events():
    subset = build_gate.l3_rule([(42, _audit(["a", "b"]), _audit(["a"]))], [42])
    assert (subset["status"], subset["source_state"]) == ("pass", "dirty on seeds [42]: not promotable without a "
                                                                   "human")
    collided = build_gate.l3_rule([(42, _audit(collisions=1), _audit(collisions=1))], [42])
    assert collided["result"] == "identities_unavailable" and collided["status"] == "blocked"
    assert build_gate.IDENTITY_REASON in collided["seeds"][0]["reason"]
    hidden = build_gate.l3_rule([(42, _audit(teleports=2), _audit(["x"]))], [42])
    assert hidden["result"] == "identities_unavailable"
    assert build_gate.screening({"pass": True}, {"verdict": "improved"}, hidden)[0] == "dynamic_unverified"
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


def test_screening_never_says_improved_while_l2_has_not_run():
    # Review finding: L2 was not implemented and the gate had no L2 field, yet screening said improved.
    passing = build_gate.l3_rule([(42, _audit(), _audit())], [42])
    label, reason = build_gate.screening({"pass": True}, {"verdict": "improved"}, passing)
    assert (label, reason) == ("improved_l2_not_run", build_gate.L2_REASON)
    assert build_gate.screening({"pass": True}, {"verdict": "improved"}, passing, {"status": "pass"}) == (
        "improved", None)
    assert build_gate.l2_not_run()["status"] == "not_run" and "improved" not in build_gate.ACCEPTED_SCREENINGS[1:]


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
    step1 = _judge(tmp_path, built, first, operation=speed, seeds=True, parent_profile=source, out=tmp_path / "c1")
    assert (step1["status"], step1["gate"]["screening"]) == ("measured", "improved_l2_not_run")
    assert step1["gate"]["L2"]["status"] == "not_run" and step1["gate"]["L3"]["result"] == "subset_holds"
    assert step1["inputs"]["parent_profile"]["subject"] == "source"
    assert step1["inputs"]["parent_net"]["scorecard"].startswith("goal-create scorecard")
    second = copy.deepcopy(first)
    _lane(second, "S_in_0").set("width", "3.2")
    first_net = Path(step1["inputs"]["net"]["path"])
    step2 = _judge(tmp_path, built, second, operation=width, seeds=True, parent_net=first_net,
                   parent_profile=tmp_path / "c1" / "profile.full.json", out=tmp_path / "c2")
    assert step2["V"]["cand"] == [0, 0, 0, 0, 0.0] and step2["inputs"]["parent_profile"]["depth"] == 1
    assert step2["inputs"]["parent_net"]["scorecard"] == "rescored: the parent is not the goal source"
    # A raw net, never judged, cannot become a parent; neither can a G0-rejected candidate.
    raw = _written(built, tmp_path / "raw", net=_write(first, tmp_path / "raw.net.xml"))
    with pytest.raises(ValueError, match="raw_net profile is never a parent"):
        _judge(tmp_path, built, second, operation=width, parent_net=tmp_path / "raw.net.xml", parent_profile=raw)
    zigzag = copy.deepcopy(faulted)
    _lane(zigzag, "W_in_1").set("shape", " ".join(f"{-100 + 0.5 * n},{0.4 if n % 2 else 0.0}" for n in range(191)))
    rejected = _judge(tmp_path, built, zigzag, operation=speed, seeds=True, out=tmp_path / "bad")
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
    assert (code, judged["status"], judged["screening"]) == (0, "measured", "improved_l2_not_run")
    assert judged["V"] == [0, 0, 0, 0, 0.0] and judged["V_parent"] == [0, 1, 1, 0, 4.0]
    assert all(judged["invariants"].values()) and (tmp_path / "p1.verify-edit" / "edit-verification.json").is_file()
    full = json.loads((tmp_path / "p1" / "profile.full.json").read_text(encoding="utf-8"))
    assert full["delta"]["fixed"] == ["lane:middle_0:centerline", "lane:middle_0:stop_line"]
    assert full["delta"]["hotspots_gone"] == ["H:CTL:lane:middle_0", "H:GEO:edge:middle"]
    assert full["gate"]["outside_fact_changes"] == []
    (seed,) = full["gate"]["L3"]["seeds"]
    assert full["gate"]["L3"]["result"] == "subset_holds" and seed["trips"]["source"] == seed["trips"]["goal"]
    assert seed["trips"]["candidate"] == seed["trips"]["goal"] is not None
    assert full["inputs"]["parent_net"]["scorecard"].startswith("goal-create scorecard")
