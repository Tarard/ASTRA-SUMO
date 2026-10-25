"""Golden M1 values of the offline Calibrate profiler, read from tracked benchmark files with zero SUMO runs."""

from __future__ import annotations

import ast
import copy
import functools
import gzip
import hashlib
import importlib.util
import io
import json
import os
import subprocess
import sys
import xml.etree.ElementTree as ET
from contextlib import redirect_stdout
from pathlib import Path

import pytest
import sumolib
from pydantic import ValidationError

from astra_sumo.core.benchmark_references import (BenchmarkReferenceError, PacketCase, read_tracked_file,
                                                  resolve_packet_file, resolve_packet_reference)
from astra_sumo.twin import bounds, count_profile, doctor, goal, ids, invariants, render

ROOT = Path(__file__).resolve().parents[1]
PACKET = "benchmarks/hamburg_ring1_chronological_od_feedback_20261024_v1"
CASE = "h4_counter_z11_minus5_z5"
GOAL_ID = "calib-ring1-20261019-h4z11m5z5-v1"
HOLD_NODES = ["14398547", "205069138", "18653534", "LSA104_part0"]
Z13, Z12, Z4_535 = "hh_0119_Z_13_34018", "hh_0119_Z_12_31546", "hh_0535_Z_4_33055"


@pytest.fixture(scope="module")
def reader() -> PacketCase:
    return goal.case_reader(ROOT, PACKET, CASE)


@pytest.fixture(scope="module")
def created(tmp_path_factory) -> tuple[goal.CalibrateGoal, bytes, Path]:
    store = tmp_path_factory.mktemp("twin-store")
    path, state = goal.write_goal(goal.create_calibrate_goal(ROOT, PACKET, CASE), store)
    assert state == "created"
    loaded, data = goal.load_goal(path)
    return loaded, data, path


@pytest.fixture(scope="module")
def built(created, reader) -> tuple[dict, dict]:
    loaded, data, _ = created
    return count_profile.build_calibrate_profile(loaded, data, reader, run_dir="evaluation/native",
                                                 verifier={"commit": "0" * 40, "clean": True})


@pytest.fixture(scope="module")
def production_inputs(reader) -> dict:
    data = {name: reader.read(f"prepared/{name}") for name in goal.PREPARED_FILES}
    network = count_profile.parse_network(data["network.net.xml"], data["control.add.xml"])
    requests = count_profile.parse_demand(data["demand.rou.xml"])
    loops = count_profile.parse_field_loops(data["field-loops.add.xml"])
    intended = count_profile.parse_intentions(data["lane-intentions.json"])
    return {"network": network, "requests": requests, "loops": loops, "intended": intended,
            "arrivals": count_profile.free_flow_arrivals(network, requests, intended, loops),
            "routes": count_profile.parse_vehicle_routes(reader.read("evaluation/native/vehicle-routes.xml")),
            "passages": count_profile.read_passage_report(reader, goal.PARENT_PASSAGES)["passages"]}


def _independent_free_flow() -> dict[str, float]:
    """a_v written separately from twin.count_profile: sumolib's net model and this test's own file reads."""
    packet = ROOT / PACKET
    aliases = json.loads((packet / "byte-references.json").read_text(encoding="utf-8"))["aliases"]

    def tracked(name: str) -> Path:
        row = aliases[f"cases/{CASE}/prepared/{name}"]
        path = (packet if row["scope"] == "packet" else ROOT) / (row["target"] + ".gz")
        assert hashlib.sha256(gzip.decompress(path.read_bytes())).hexdigest() == row["sha256"]
        return path

    def unpack(name: str) -> bytes:
        return gzip.decompress(tracked(name).read_bytes())

    net = sumolib.net.readNet(str(tracked("network.net.xml")), withInternal=True)
    loops = {e.get("id"): (e.get("lane"), float(e.get("pos")))
             for e in ET.fromstring(unpack("field-loops.add.xml")).iter("inductionLoop")}
    intent = {r["vehicle_id"]: r["field_id"] for r in json.loads(unpack("lane-intentions.json"))["vehicles"]}

    @functools.lru_cache(maxsize=None)
    def via_chain_s(lane_id: str) -> float:
        lane = net.getLane(lane_id)
        onward = [c.getViaLaneID() for c in lane.getOutgoing() if c.getViaLaneID()]
        return lane.getLength() / lane.getSpeed() + (min(map(via_chain_s, onward)) if onward else 0.0)

    @functools.lru_cache(maxsize=None)
    def junction_s(source: str, target: str) -> float:
        links = net.getEdge(source).getOutgoing().get(net.getEdge(target), [])
        return min((via_chain_s(c.getViaLaneID()) if c.getViaLaneID() else 0.0) for c in links) if links else 0.0

    def run_s(edge_id: str, offset: float = 0.0) -> float:
        lanes = net.getEdge(edge_id).getLanes()
        return max(0.0, min(lane.getLength() for lane in lanes) - offset) / max(lane.getSpeed() for lane in lanes)

    result = {}
    for vehicle in ET.fromstring(unpack("demand.rou.xml")).iter("vehicle"):
        name = vehicle.get("id")
        lane_id, pos = loops[intent[name]]
        target_lane = net.getLane(lane_id)
        edges = vehicle.find("route").get("edges").split()
        depart, start = float(vehicle.get("depart")), float(vehicle.get("departPos"))
        stop = edges.index(target_lane.getEdge().getID())
        if stop == 0:
            result[name] = depart + max(0.0, pos - start) / target_lane.getSpeed()
            continue
        upstream = run_s(edges[0], start) + sum(run_s(edge) for edge in edges[1:stop])
        junctions = sum(junction_s(a, b) for a, b in zip(edges[:stop], edges[1:stop + 1]))
        result[name] = depart + upstream + junctions + pos / target_lane.getSpeed()
    return result


def test_resolver_reads_aliases_through_tracked_gz_targets_and_checks_raw_sha(reader):
    aliases = reader.aliases
    for name in goal.PREPARED_FILES:
        key = f"cases/{CASE}/prepared/{name}"
        reference = resolve_packet_reference(ROOT / PACKET, key)
        assert reference.scope == aliases[key]["scope"] and reference.tracked_path.name.endswith(".gz")
        assert reference.sha256 == aliases[key]["sha256"] == hashlib.sha256(reference.data).hexdigest()
        assert resolve_packet_file(ROOT / PACKET, key) == reference.data
    report = reader.resolve(goal.PARENT_PASSAGES)
    assert report.scope == "direct" and report.tracked_path.name.endswith(".json.gz.gz")
    assert report.data[:2] == b"\x1f\x8b"
    assert json.loads(reader.read("evaluation/contributing-passages.json"))["reconciled"] is True


def test_resolver_refuses_unsafe_undeclared_and_mismatched_references(tmp_path):
    packet = tmp_path / "benchmarks" / "packet"
    packet.mkdir(parents=True)
    (tmp_path / "data.txt.gz").write_bytes(gzip.compress(b"payload"))
    good = hashlib.sha256(b"payload").hexdigest()
    (packet / "byte-references.json").write_text(json.dumps({"aliases": {
        "cases/c/good.txt": {"scope": "repository", "target": "data.txt", "sha256": good},
        "cases/c/bad.txt": {"scope": "repository", "target": "data.txt", "sha256": "0" * 64},
        "cases/c/chain.txt": {"scope": "packet", "target": "cases/c/good.txt", "sha256": good}}}), encoding="utf-8")
    assert resolve_packet_file(packet, "cases/c/good.txt") == b"payload"
    assert resolve_packet_reference(packet, "cases/c/chain.txt").scope == "packet"
    with pytest.raises(BenchmarkReferenceError, match="declared"):
        resolve_packet_file(packet, "cases/c/bad.txt")
    with pytest.raises(BenchmarkReferenceError, match="Unsafe"):
        resolve_packet_file(packet, "../data.txt")
    with pytest.raises(FileNotFoundError):
        resolve_packet_file(packet, "cases/c/missing.txt")
    (tmp_path / "twice.bin.gz.gz").write_bytes(gzip.compress(gzip.compress(b"inner")))
    assert read_tracked_file(tmp_path / "twice.bin")[0] == b"inner"


def test_resolve_reports_aliases_baseline_passages_holds_and_timeouts():
    report = goal.resolve_calibrate_case(ROOT, PACKET, CASE)
    assert report["status"] == "pass" and report["pass"] and all(report["checks"].values())
    assert report["checks"]["all_stopped_timeout"] and "rule" in report["resolver"]
    assert [row["name"] for row in report["aliases"]] == list(goal.PREPARED_FILES)
    assert all(row["match"] and row["tracked"].endswith(".gz") for row in report["aliases"])
    assert len(report["baseline_run"]) == 9 and all(row["present"] for row in report["baseline_run"])
    assert report["passages"]["reconciled"] is True and report["passages"]["inputs_unchanged"] is True
    assert report["hold_nodes"]["equal"] and report["hold_nodes"]["sources"]["run.json"] == HOLD_NODES
    timeouts = report["timeouts"]
    assert timeouts["timeout_s"] == 4800 and timeouts["all_stopped_timeout_s"] == 300
    assert timeouts["timeout_source"]["line"] == 102 and timeouts["timeout_source"]["target"] == (
        "benchmarks/hamburg_ring1_early_quantity_feedback_20261024_v1/cases/window0/measurement-implementation/"
        "run_case.py")
    assert timeouts["timeout_source"]["sha256"].startswith("a64a03b8")


def test_goal_copies_run_block_from_machine_fields_and_is_strict(created, tmp_path):
    loaded, data, path = created
    assert loaded.goal_id == GOAL_ID and path.parent.name == GOAL_ID and path.parent.parent.name == "goals"
    run = loaded.run
    assert (run.backend, run.sumo, run.seed, run.step_s, run.end_s) == ("libsumo", "1.27.1", 104, 0.1, 14400)
    assert run.junction_body_hold_nodes == HOLD_NODES and run.junction_body_hold and run.lane_intentions
    assert (run.all_stopped_timeout_s, run.timeout_s) == (300, 4800)
    assert run.recorded_options["--time-to-teleport"] == "-1" and run.recorded_options["--precision"] == "12"
    assert loaded.inputs.files["network.net.xml"].startswith("6c52382e") and len(loaded.inputs.files) == 7
    assert loaded.baseline.model_dump() == {"tae_main": 275, "sse": 2833, "mae": 2.115384615, "max": 22, "exact": 83,
                                            "init52_tae": 97, "all182_tae": 372, "recorded": loaded.baseline.recorded}
    observation = loaded.observation
    assert observation.complete == {"main": "130/130", "init": "52/52"} and observation.time_alignment == "182/182"
    assert observation.lineage.main_rows_equal == "130/130"
    assert observation.lineage.main_sha256.startswith("ae218727")
    placement = observation.e1_before_lane_end_m
    assert sum(value <= 0.11 for value in placement.values()) == 12 and placement["0535Z14"] == 1.398
    assert observation.uncounted["early_departures"] == 20 and observation.uncounted["intended"] == 0
    assert loaded.variable_class.cells == {"od_pairs": 22, "periods": 14, "cells": 308, "non_empty": 295,
                                           "multi_field": 112}
    assert loaded.gate_protocol.provisional.m_row == 3 and loaded.gate_protocol.k3.magnitude_grid_s["large"] == 900
    document = json.loads(data)
    with pytest.raises(ValidationError):
        goal.CalibrateGoal.model_validate({**document, "unexpected": 1})
    with pytest.raises(ValidationError):
        goal.CalibrateGoal.model_validate({**document, "gate_protocol": {**document["gate_protocol"], "k3": {}}})
    store = tmp_path / "store"
    assert goal.write_goal(loaded, store)[1] == "created" and goal.write_goal(loaded, store)[1] == "unchanged"
    with pytest.raises(FileExistsError, match="new goal_id"):
        goal.write_goal(loaded.model_copy(update={"claim_boundary": "changed"}), store)
    # A frozen goal serialization: any change to the declared text or the copied fields changes this reference.
    assert ids.goal_reference(GOAL_ID, data) == f"{GOAL_ID}@2ba4c172"
    for bad in ("calib-x.", "calib-x-", "x" * 101, "con", "nul.goal", "Calib-x"):
        with pytest.raises(ValidationError):
            goal.CalibrateGoal.model_validate({**document, "goal_id": bad})
    assert goal.CalibrateGoal.model_validate({**document, "goal_id": "x" * 100}).goal_id == "x" * 100


def test_incomplete_or_untargeted_rows_are_refused_not_imputed(reader):
    main_rows = reader.read_csv("prepared/expected-counts.csv")
    init_rows = reader.read_csv("prepared/initialization-counts.csv")
    field_counts = reader.read("evaluation/native/field-counts.xml")
    short = [dict(row) for row in main_rows]
    short[0]["source_observation_count"] = "2"
    assert count_profile.incomplete_rows(short) == [ids.row_id(short[0]["detector_id"], float(short[0]["begin"]))]
    with pytest.raises(ValueError, match="incomplete source observations"):
        count_profile.score_counts(short, init_rows, field_counts)
    with pytest.raises(ValueError, match="without target rows: f \\(init\\)"):
        count_profile.field_balance(["f"], {}, [], {}, {"f": 1}, {}, init_end_s=0.0, main_end_s=1.0)
    with pytest.raises(ValueError, match="invalid exitTimes"):
        count_profile.parse_vehicle_routes(b'<routes><vehicle id="v" depart="0"><route edges="a b" exitTimes="1"/>'
                                           b'</vehicle></routes>')
    with pytest.raises(ValueError, match="no embedded route"):
        count_profile.parse_demand(b'<routes><vehicle id="v" depart="0"/></routes>')


def test_score_reproduces_baseline_through_core_comparison(built):
    score = built[0]["score"]
    assert (score["tae_main"], score["sse"], score["max"], score["exact"]) == (275, 2833, 22, 83)
    assert score["mae"] == pytest.approx(2.1153846, abs=5e-8)
    assert (score["init52_tae"], score["all182_tae"], score["rows_main"], score["rows_init"]) == (97, 372, 130, 52)
    assert score["geh_raw"] == {"lt5_percent": 100.0, "max": 3.24}
    assert (score["geh_hourly"]["lt5_percent"], score["geh_hourly"]["max"]) == (99.23, 6.49)
    main = [row for row in built[1]["rows.csv"] if row["window"] == "main"]
    assert len(main) == 130 and all(row["geh_hourly"] == pytest.approx(2 * row["geh"], abs=2e-6) for row in main)


def test_separately_written_free_flow_routine_agrees_on_every_vehicle(production_inputs):
    production = production_inputs["arrivals"]
    independent = _independent_free_flow()
    assert len(production) == len(independent) == 8589 and production.keys() == independent.keys()
    assert max(abs(production[v] - independent[v]) for v in production) < 1e-6


def test_anchor_excess_sums_to_delay_against_free_flow(production_inputs):
    inputs = production_inputs
    worst = 0.0
    for passage in inputs["passages"]:
        vehicle = passage["vehicle_id"]
        excess = count_profile._excess_profile(inputs["network"], inputs["requests"][vehicle],
                                               inputs["routes"][vehicle], inputs["loops"][passage["detector_id"]],
                                               passage["time_s"])
        worst = max(worst, abs(sum(excess.values()) - (passage["time_s"] - inputs["arrivals"][vehicle])))
    assert worst < 1e-6


def test_decomposition_golden(built):
    decomp = built[0]["decomp"]
    assert (decomp["ontime"], decomp["late_in"], decomp["delayed_out"], decomp["supply_term_abs"]) == (
        5770, 902, 866, 53)
    assert (decomp["ff_violations"], decomp["passages"]) == (0, 8589)
    assert decomp["identity_tae"] == 275 and decomp["identity_rows"] == "130/130"
    assert {k: round(v) for k, v in decomp["delay_vs_ff_s"].items()} == {"p50": 126, "p90": 375, "p99": 517}
    rows = {(row["detector_id"], row["begin_s"]): row for row in built[1]["decomposition.csv"]}

    def split(key: tuple[str, float]) -> tuple[int, ...]:
        return tuple(rows[key][k] for k in ("y", "planned", "measured", "ontime", "late_in", "delayed_out"))

    assert split((Z13, 3600.0)) == (145, 145, 141, 109, 32, 36)
    assert split((Z12, 3600.0)) == (119, 119, 114, 91, 23, 28)
    assert split((Z4_535, 10800.0)) == (57, 57, 35, 35, 0, 22)
    assert split((Z13, 11700.0)) == (118, 103, 138, 99, 39, 4)
    assert rows[(Z4_535, 10800.0)]["delayed_out_measured_in"] == "11700:22"


def test_field_balance_golden(built):
    balance = built[0]["field_balance"]
    fields = balance["fields"]
    assert {name: row["deficit"] for name, row in fields.items() if row["deficit"]} == {
        "0119Z11": 2, "0119Z12": 15, "0119Z13": 15, "0119Z4": 1, "0119Z5": 6, "0535Z4": 12}
    assert {name: row["post_window"] for name, row in fields.items() if row["post_window"]} == {
        "0119Z12": 3, "0119Z13": 4, "0119Z4": 9, "0119Z5": 12, "0535Z4": 1}
    assert (balance["deficit_total"], balance["post_window"], balance["unpassed"]) == (51, 29, 0)
    assert balance["identity_fields"] == "13/13" and balance["ff_init_equals_init_targets"] == "13/13"
    assert (fields["0119Z13"]["r_init"], fields["0119Z13"]["r_main"]) == (-32, 13)
    assert (balance["requests"], balance["targets"]) == (8589, 8640)


def test_bounds_carry_verified_conditions_and_are_reported_only(built):
    profile = built[0]
    b1, b2 = profile["bounds"]["B1_all182_request_floor"], profile["bounds"]["B2_main_supply"]
    assert b1["value"] == 51 and b1["self_check"] == {"all182_tae": 372, "satisfied": True}
    assert b2["value"] == 12 and b2["per_field"] == {"0119Z5": 1, "0535Z4": 11}
    assert b2["at_init_targets"]["value"] == 52 and b2["at_init_targets"]["per_field"]["0119Z4"] == 2
    assert "0119Z4: 2 at init targets vs deficit 1" in b2["note"]
    for bound in (b1, b2):
        assert bound["label"] == "conditional" and bound["use"] == bounds.USE and bound["verified_on_parent"]
        assert bound["conditions"] and all(set(c) == {"condition", "verified", "evidence"} and c["verified"]
                                           for c in bound["conditions"])
    conditions = [c["condition"] for c in b2["conditions"]]
    assert "a valid passage-time lower bound: no passage earlier than a_v" in conditions
    assert [sol["value"] for sol in profile["bounds"]["SOL"]] == [0.0, 0.0]
    assert [c["verified"] for c in b2["conditions"]] == [True] * 4
    assert "8589/8589 intended vehicles" in b2["conditions"][0]["evidence"]
    assert "13/13 fields" in b2["conditions"][2]["evidence"]
    failing = {"id": "INV-PASS", "pass": False, "witness_ids": ["report:reconciled"]}
    rows = [{"field": "F", "detector_id": "f", "deficit": 2, "requests": 3, "m_init": 0, "e_init": 0}]
    matched = {"demand": 3, "intentions": 3, "demand_without_intention": 0, "intention_without_demand": 0}
    assert not bounds.request_floor_b1(rows, demand_match=matched, inv_pass=failing,
                                       all182_tae=9)["verified_on_parent"]
    extra = bounds.request_floor_b1(rows, demand_match={**matched, "demand": 4, "demand_without_intention": 1},
                                    inv_pass={"pass": True}, all182_tae=9)
    assert [c["verified"] for c in extra["conditions"]] == [False, True] and not extra["verified_on_parent"]
    late = bounds.main_supply_b2(rows, {"f": [(900.0, 4)]}, {"f": [10.0, 20.0, 950.0]}, intended_vehicles=3,
                                 e1_init={"f": 0}, ff_violations=1, passages=3, inv_pass={"pass": True}, main_tae=3)
    assert late["value"] == 2 and not late["verified_on_parent"] and late["self_check"]["satisfied"]
    assert [c["verified"] for c in late["conditions"]] == [True, False, True, True]
    other = bounds.main_supply_b2(rows, {"f": [(900.0, 4)]}, {"f": [10.0]}, intended_vehicles=3, e1_init={"f": 1},
                                  ff_violations=0, passages=1, inv_pass={"pass": True}, main_tae=3)
    assert [c["verified"] for c in other["conditions"]] == [False, True, False, True]


def test_no_rule_consumes_a_bound(created, reader, monkeypatch):
    package = ROOT / "plugins" / "astra-sumo" / "src" / "astra_sumo" / "twin"
    importers = sorted(path.name for path in package.glob("*.py") if any(
        isinstance(node, (ast.Import, ast.ImportFrom)) and "bounds" in {a.name for a in node.names}
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8")))))
    assert importers == ["count_profile.py"]
    absurd = {"label": "conditional", "value": 10 ** 6, "conditions": [], "verified_on_parent": False,
              "self_check": {"all182_tae": 0, "main_tae": 0, "satisfied": False}, "use": bounds.USE,
              "at_init_targets": {"value": 10 ** 6}, "note": ""}
    monkeypatch.setattr(bounds, "request_floor_b1", lambda *a, **k: absurd)
    monkeypatch.setattr(bounds, "main_supply_b2", lambda *a, **k: absurd)
    loaded, data, _ = created
    profile, _ = count_profile.build_calibrate_profile(loaded, data, reader, run_dir="evaluation/native",
                                                       verifier={"commit": "0" * 40, "clean": True})
    assert profile["status"] == "measured" and all(check["pass"] for check in profile["invariants"])


def test_localization_against_route_matched_peers(built):
    rows = {row["row_id"]: row for row in built[0]["localization"]["rows"]}
    z13 = rows["R:0119Z13@3600"]
    assert (z13["localization"], z13["entry"], z13["n_delayed"], z13["n_peer"]) == ("localized", "44532531", 36, 109)
    assert "status" not in z13 and all("status" not in row for row in rows.values())
    assert (z13["coanchors"][0]["window_s"], z13["next"]["window_s"]) == (4380, 4500)
    assert z13["n_delayed_other_prefix"] == 0 and z13["excluded_peers_by_entry"] == {}
    assert z13["hotspot_id"] == "H:LOC:entry:44532531" and len(z13["coanchors"]) == 1
    insertion = z13["coanchors"][0]
    assert (insertion["anchor"], insertion["diff"], insertion["mean_delayed"], insertion["mean_peer"]) == (
        "insertion", 185.1, 251.4, 66.3)
    assert (z13["next"]["anchor"], z13["next"]["diff"]) == ("199500674#0", 8.9)
    assert z13["next"]["flags"] == ["actuated TLS 6390683114: 3 candidate links to 199500674#1"]

    z4 = rows["R:0535Z4@10800"]
    assert (z4["entry"], z4["n_delayed"], z4["n_peer"]) == ("-983227468#1", 22, 7)
    assert z4["excluded_peers_by_entry"] == {"771709146#0": 28} and z4["n_delayed_other_prefix"] == 0
    co = [(a["anchor"], a["node"], a["diff"], a["mean_delayed"], a["mean_peer"]) for a in z4["coanchors"]]
    assert co == [("551303424", "205069138", 64.3, 126.7, 62.4), ("-186713427", "14398547", 55.4, 55.4, 0.0),
                  ("551303409", "265093112", 51.2, 51.5, 0.3)]
    assert [a["window_s"] for a in z4["coanchors"]] == [11760, 11400, 11520]
    flags = [a["flags"] for a in z4["coanchors"]]
    assert flags == [[count_profile.HOLD_FLAG, "actuated TLS 205069138: 4 candidate links to 189775480"],
                     [count_profile.HOLD_FLAG], []]
    assert z4["hotspot_id"] == ids.coanchor_hotspot_id(["edge:-186713427", "edge:551303409", "edge:551303424"])
    spread = z4["spread"]
    assert (spread["from"], spread["to"], spread["edges"]) == ("-186713425", "551303424", 12)
    assert spread["downstream"] == [["189775480", -62.6], ["186821034#0", -18.4]]
    assert spread["downstream_sum_s"] == -81.0
    # The exact sum is 293.248 s; the design's +293.1 s is the sum of the twelve one-decimal differentials.
    assert spread["sum_s"] == 293.2
    span = [r for r in built[1]["localization.csv"] if r["row_id"] == "R:0535Z4@10800" and r["anchor"] != "insertion"]
    names = [r["anchor"] for r in span]
    segment = span[names.index("-186713425"): names.index("551303424") + 1]
    assert round(sum(round(r["diff_s"], 1) for r in segment), 1) == 293.1
    assert round(sum(r["diff_s"] for r in segment), 3) == 293.248
    assert z4["delayed_measured_in"] == {"11700": 22}
    assert built[0]["inputs"]["hold_nodes"] == HOLD_NODES


def test_integrity_rows_from_recorded_stderr(built):
    integrity = built[0]["integrity"]
    assert integrity["healthy"] and integrity["arrived"] == "8589/8589"
    assert integrity["emergency_braking"]["count"] == integrity["emergency_braking"]["summary_count"] == 5
    actuated = integrity["actuated_phase_without_controlling_detector"]
    assert (actuated["warnings"], actuated["phases"], actuated["tl_logics"], actuated["reports_per_phase"]) == (
        20, 10, 9, [2])
    assert integrity["missing_green_phase"] == {"count": 2, "witness_ids": ["LSA104_part0@readme_fixed_A#14",
                                                                            "LSA535_part0@readme_fixed_A#5"]}
    assert integrity["unused_states"]["count"] == 5 and integrity["unclassified_warnings"] == 0
    assert (integrity["early_departures"]["count"], integrity["early_departures"]["intended"]) == (20, 0)


def test_invariants_pass_on_parent_and_name_witnesses_on_violation(built):
    checks = built[0]["invariants"]
    assert [c["id"] for c in checks] == ["INV-INPUTS", "INV-OBS", "INV-HOLD", "INV-PASS"]
    assert all(set(c) == {"id", "pass", "witness_ids"} and c["pass"] and c["witness_ids"] == [] for c in checks)
    assert invariants.inv_inputs({"a": "1", "b": "2"}, {"a": "1", "b": "3"})["witness_ids"] == ["b"]
    loop = count_profile.FieldLoop("e_0", 10.0, 900.0)
    obs = invariants.inv_obs(goal_loops={"d": loop}, run_loops={"d": count_profile.FieldLoop("e_0", 9.0, 900.0)},
                             goal_attribute="nVehContrib", run_attributes=["nVehEntered"], goal_loops_sha256="x",
                             run_loops_sha256="y")
    assert obs == {"id": "INV-OBS", "pass": False,
                   "witness_ids": ["attribute:nVehEntered", "detector:d", "file:field-loops.add.xml"]}
    hold = invariants.inv_hold(HOLD_NODES, {"run.json": HOLD_NODES[:3], "native-request.json": None})
    assert hold["witness_ids"] == ["missing:native-request.json", "node:LSA104_part0"]
    report = {"reconciled": False, "inputs_unchanged": True, "passages": [
        {"vehicle_id": "v", "detector_id": "d"}, {"vehicle_id": "v", "detector_id": "d"},
        {"vehicle_id": "w", "detector_id": "d"}]}
    assert invariants.inv_pass(report, {"v": "d", "w": "e"})["witness_ids"] == [
        "off_intention:w@d", "repeated:v@d", "report:reconciled"]


def test_compact_views_are_capped_and_list_top_hotspots(built):
    profile = {**built[0], "details": {}}
    compact_json, compact_text = render.render_compact(profile)
    assert len(compact_json) <= render.CAP_CHARACTERS and len(compact_text) <= render.CAP_CHARACTERS
    view = json.loads(compact_json)
    assert view["view"] == "compact" and view["status"] == "measured" and len(view["hot"]) == 5
    assert (view["goal"], view["cand"], view["verifier"]) == (f"{GOAL_ID}@2ba4c172", "c-4f35cd291717", "0" * 40)
    assert [(hotspot["id"], hotspot["w"]) for hotspot in view["hot"]] == [
        ("H:LOC:entry:44532531", 58), ("H:LOC:edge:24483344#1", 33), ("H:LOC:co:27d1dde6", 22),
        ("H:LOC:edge:24483344#0", 21), ("H:BAL:field:0119Z13", 13)]
    assert len(view["hot"][0]["rows"]) == 7 and view["hot"][0]["rows"]["more"] == "+21 more"
    assert view["hot_more"] == "+10 more" and "R:0535Z4@10800" in view["hot"][2]["rows"]
    assert "TAE 275" in compact_text and compact_text.count("\nHOT") == 6
    dirty = json.loads(render.render_compact({**profile, "verifier": {"commit": "a" * 40, "clean": False}})[0])
    assert dirty["verifier"] == "a" * 40 + "+dirty"
    with pytest.raises(ValueError, match="exceeds"):
        render.render_compact(profile, cap=500)


def test_compact_cap_holds_with_long_shown_ids(built):
    """Lengthen what both views show; one budget serves both, and the clipped budget bounds them below the cap."""

    def stretch(text: str, size: int) -> str:
        return f"{text}-{'x' * size}"

    def inflate(size: int) -> dict:
        profile = copy.deepcopy({**built[0], "details": {}})
        for hotspot in profile["hotspots"][:render.HOTSPOTS]:
            hotspot["id"] = stretch(hotspot["id"], size)
            for row in hotspot["rows"]:
                key = "row_id" if "row_id" in row else "field"
                row[key] = stretch(row[key], size)
            if lead := hotspot.get("lead"):
                lead["entry"] = stretch(lead["entry"], size)
                for anchor in lead["coanchors"]:
                    anchor.update({key: stretch(anchor[key], size) for key in ("anchor", "node", "element")})
                if lead["spread"]:
                    lead["spread"]["downstream"] = [[stretch(edge, size), value]
                                                    for edge, value in lead["spread"]["downstream"]] * 5
        balance = profile["field_balance"]
        balance["fields"] = {stretch(name, size): row for name, row in balance["fields"].items()} | {
            stretch(f"extra{number}", size): {**row, "deficit": 1} for number, row in
            enumerate(list(balance["fields"].values()) * 2)}
        profile["inputs"]["hold_nodes"] = [stretch(node, size) for node in profile["inputs"]["hold_nodes"]] * 5
        profile["goal"] = stretch(profile["goal"], size)
        profile["invariants"] = [{**check, "pass": False, "witness_ids": [stretch(f"w{n}", size) for n in range(9)]}
                                 for check in profile["invariants"]]
        return profile

    for size in (150, 400, 3000):
        compact_json, compact_text = render.render_compact(inflate(size))
        assert len(compact_json) <= render.CAP_CHARACTERS and len(compact_text) <= render.CAP_CHARACTERS
        view = json.loads(compact_json)
        assert compact_text.count("\nHOT") == len(view["hot"]) + ("hot_more" in view)
    assert view["hot"] == [] and len(view["goal"]) == render.CLIP_ID and "~" in view["goal"]
    assert len(view["inputs"]["hold_nodes"]) == render.CLIP_ITEMS + 1 and len(view["field_balance"]["fields"]) == 10


def test_profile_directory_is_new_deterministic_and_hash_referenced(created, reader, tmp_path):
    loaded, data, _ = created
    outputs = []
    for name in ("first", "second"):
        out = tmp_path / name
        profile = count_profile.write_calibrate_profile(loaded, data, reader, run_dir="evaluation/native",
                                                        out_dir=out, verifier={"commit": "0" * 40, "clean": True})
        outputs.append(out)
    assert sorted(p.name for p in outputs[0].iterdir()) == ["decomposition.csv", "field-balance.csv",
                                                            "localization.csv", "profile.compact.json",
                                                            "profile.compact.txt", "profile.full.json", "rows.csv"]
    for path in outputs[0].iterdir():
        other = outputs[1] / path.name
        if path.name == "profile.full.json":
            first, second = (json.loads(p.read_text(encoding="utf-8")) for p in (path, other))
            assert first.pop("timing") and second.pop("timing") and first == second
        else:
            assert path.read_bytes() == other.read_bytes(), path.name
    for name, row in profile["details"].items():
        assert row["sha256"] == hashlib.sha256((outputs[1] / name).read_bytes()).hexdigest()
    assert profile["compact"]["profile.compact.json"]["characters"] <= render.CAP_CHARACTERS
    with pytest.raises(FileExistsError):
        count_profile.write_calibrate_profile(loaded, data, reader, run_dir="evaluation/native", out_dir=outputs[0],
                                              verifier={})
    with pytest.raises(ValueError, match="baseline run"):
        count_profile.build_calibrate_profile(loaded, data, reader, run_dir="evaluation", verifier={})


def test_a_failed_profile_write_leaves_no_directory_and_allows_a_rerun(created, built, reader, tmp_path, monkeypatch):
    loaded, data, _ = created
    monkeypatch.setattr(count_profile, "build_calibrate_profile", lambda *args, **kwargs: copy.deepcopy(built))

    def fail(*args, **kwargs):
        raise OSError("disk full")

    out = tmp_path / "profiles" / "run"
    with monkeypatch.context() as patch:
        patch.setattr(count_profile, "write_json_atomic", fail)
        with pytest.raises(OSError, match="disk full"):
            count_profile.write_calibrate_profile(loaded, data, reader, run_dir="evaluation/native", out_dir=out,
                                                  verifier={})
    assert list((tmp_path / "profiles").iterdir()) == []
    profile = count_profile.write_calibrate_profile(loaded, data, reader, run_dir="evaluation/native", out_dir=out,
                                                    verifier={})
    assert profile["status"] == "measured" and [p.name for p in (tmp_path / "profiles").iterdir()] == ["run"]
    assert (out / "profile.full.json").is_file() and len(list(out.iterdir())) == 7


def _cli_run(cli, argv: list[str]) -> tuple[int, dict]:
    stream = io.StringIO()
    with redirect_stdout(stream):
        code = cli.main(argv)
    return code, json.loads(stream.getvalue())


def test_cli_goal_create_and_profile(tmp_path):
    spec = importlib.util.spec_from_file_location("astra_twin_cli", ROOT / "plugins/astra-sumo/scripts/astra_twin.py")
    cli = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cli)
    assert Path(sys.path[0]) == cli.SRC_ROOT and sys.path.count(str(cli.SRC_ROOT)) == 1
    store = tmp_path / "store"
    goal_file = str(store / "goals" / GOAL_ID / "goal.json")
    profile_args = ["profile", "calibrate", "--goal", goal_file, "--run", "packet:evaluation/native", "--out",
                    str(tmp_path / "profile"), "--json"]
    create_args = ["goal-create", "--capability", "calibrate", "--packet", PACKET, "--case", CASE, "--store",
                   str(store), "--json"]
    code, first = _cli_run(cli, create_args)
    assert (code, first["status"], first["write"]) == (0, "pass", "created")
    code, again = _cli_run(cli, create_args)
    assert (code, again["status"], again["write"]) == (0, "pass", "unchanged")
    assert again["goal_ref"] == first["goal_ref"] == f"{GOAL_ID}@2ba4c172"
    code, measured = _cli_run(cli, profile_args)
    assert (code, measured["status"]) == (0, "measured")
    assert measured["verifier"]["astra_sumo"] == "plugins/astra-sumo/src/astra_sumo/__init__.py"
    code, existing = _cli_run(cli, profile_args)
    assert (code, existing["status"], existing["error_type"]) == (3, "blocked", "FileExistsError")
    code, bad_run = _cli_run(cli, [*profile_args[:5], "evaluation/native", *profile_args[6:]])
    assert (code, bad_run["status"]) == (3, "blocked") and bad_run["reason"].startswith("--run must be packet:")
    code, crashed = _cli_run(cli, ["profile", "calibrate", "--goal", str(tmp_path), "--run", "packet:x", "--out",
                                   str(tmp_path / "never"), "--json"])
    assert (code, crashed["status"]) == (3, "blocked") and crashed["error_type"] in {"PermissionError",
                                                                                     "IsADirectoryError"}
    assert not (tmp_path / "never").exists()


def test_cli_imports_the_clone_ahead_of_a_shadowing_path(tmp_path):
    shadow = tmp_path / "shadow"
    (shadow / "astra_sumo").mkdir(parents=True)
    (shadow / "astra_sumo" / "__init__.py").write_text('raise ImportError("shadow astra_sumo imported")\n',
                                                       encoding="utf-8")
    script = ROOT / "plugins/astra-sumo/scripts/astra_twin.py"
    done = subprocess.run([sys.executable, "-X", "utf8", str(script), "resolve", "--packet", PACKET, "--case", CASE,
                           "--json"], cwd=tmp_path, env={**os.environ, "PYTHONPATH": str(shadow)},
                          capture_output=True, encoding="utf-8", timeout=120)
    assert done.returncode == 0, done.stdout + done.stderr
    assert json.loads(done.stdout)["status"] == "pass"
    assert doctor.foreign_sources([str(shadow), str(ROOT / "plugins/astra-sumo/src")],
                                  ROOT / "plugins/astra-sumo/src") == [str(shadow)]


def test_doctor_reports_import_paths_and_clone_state(tmp_path):
    scratch = tmp_path / "scratch"
    report = doctor.run_doctor(ROOT, forbidden_paths=[str(ROOT / "no-such-src")], expected_commit="0" * 40,
                               scratch_root=scratch)
    checks = report["checks"]
    assert report["status"] == "blocked" and not report["pass"] and checks["head_matches_expected"] is False
    assert checks["astra_sumo_in_process_under_clone"] and checks["astra_sumo_child_under_clone"]
    assert checks["forbidden_absent_from_child_sys_path"] and checks["no_foreign_astra_sumo_on_child_sys_path"]
    assert report["clone"]["commit"] and report["clone"]["expected_commit_source"] == "--expect-commit"
    assert Path(report["astra_sumo"]["child_cwd"]).resolve().parent == scratch.resolve()
    assert list(scratch.iterdir()) == []
    head = report["clone"]["commit"]
    assert doctor.commit_from_directory(tmp_path / head) == head
    assert doctor.commit_from_directory(tmp_path / "astra-impl") is None
    if report["sumo"]["binary"] is None:
        pytest.skip("SUMO binary not installed; version check not exercised")
    assert report["sumo"]["version"] == doctor.EXPECTED_SUMO and checks["libsumo_import"]
    assert checks["sumo_distributions"] and checks["sumo_binary_in_venv"]
