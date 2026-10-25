"""Golden M1 values of the offline Calibrate profiler, read from tracked benchmark files with zero SUMO runs."""

from __future__ import annotations

import ast
import copy
import dataclasses
import functools
import gzip
import hashlib
import importlib.util
import io
import json
import os
import re
import subprocess
import sys
import xml.etree.ElementTree as ET
import zlib
from contextlib import redirect_stdout
from pathlib import Path, PurePosixPath

import pytest
import sumolib
from pydantic import ValidationError

from astra_sumo.core.benchmark_references import (LOCAL_ONLY_FILE, BenchmarkReferenceError, PacketCase, git_blob_id,
                                                  head_blob_ids, read_tracked_file, resolve_packet_file,
                                                  resolve_packet_reference)
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


def test_packet_manifest_pins_the_tracked_bytes_read(tmp_path):
    """A tracked packet file is pinned only while the bytes read carry the manifest sha256 (a temp packet copy)."""
    case = tmp_path / "benchmarks" / "packet" / "cases" / "c"
    case.mkdir(parents=True)
    tracked = case / "run.json.gz"
    tracked.write_bytes(gzip.compress(b"{}"))
    (case / "unlisted.json").write_text("{}", encoding="utf-8")
    packet = case.parents[1]
    (packet / "byte-references.json").write_text(json.dumps({"aliases": {}}), encoding="utf-8")
    (packet / "manifest.json").write_text(json.dumps({"files": [
        {"path": "cases/c/run.json.gz", "sha256": hashlib.sha256(tracked.read_bytes()).hexdigest()}]}),
        encoding="utf-8")
    reader = PacketCase(packet, "c")
    reference = reader.resolve("run.json")
    assert reference.data == b"{}" and reference.scope == "direct" and reader.pinned(reference)
    assert reference.tracked_sha256 == hashlib.sha256(tracked.read_bytes()).hexdigest() != reference.sha256
    assert not reader.pinned(reader.resolve("unlisted.json"))
    tracked.write_bytes(gzip.compress(b'{"changed": true}'))
    assert not reader.pinned(reader.resolve("run.json"))


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
    assert b1["conditions"][-1] == b2["conditions"][-1] == {
        "condition": bounds.INVARIANTS_CONDITION, "verified": True,
        "evidence": "4/4 pass: INV-INPUTS, INV-OBS, INV-HOLD, INV-PASS"}
    passing = [{"id": "INV-PASS", "pass": True}]
    failing = [{"id": "INV-PASS", "pass": False, "witness_ids": ["report:reconciled"]}]
    rows = [{"field": "F", "detector_id": "f", "deficit": 2, "requests": 3, "m_init": 0, "e_init": 0}]
    matched = {"demand": 3, "intentions": 3, "demand_without_intention": 0, "intention_without_demand": 0}
    assert not bounds.request_floor_b1(rows, demand_match=matched, invariants=failing,
                                       all182_tae=9)["verified_on_parent"]
    # Any failed invariant, not only INV-PASS, unverifies a bound; so does a run checked without INV-PASS.
    for checks in ([*passing, {"id": "INV-OBS", "pass": False}], [{"id": "INV-OBS", "pass": True}]):
        floor = bounds.request_floor_b1(rows, demand_match=matched, invariants=checks, all182_tae=9)
        assert [c["verified"] for c in floor["conditions"]] == [True, False] and not floor["verified_on_parent"]
    assert bounds.request_floor_b1(rows, demand_match=matched, invariants=passing,
                                   all182_tae=9)["conditions"][-1]["evidence"] == "1/1 pass: INV-PASS"
    extra = bounds.request_floor_b1(rows, demand_match={**matched, "demand": 4, "demand_without_intention": 1},
                                    invariants=passing, all182_tae=9)
    assert [c["verified"] for c in extra["conditions"]] == [False, True] and not extra["verified_on_parent"]
    late = bounds.main_supply_b2(rows, {"f": [(900.0, 4)]}, {"f": [10.0, 20.0, 950.0]}, intended_vehicles=3,
                                 e1_init={"f": 0}, ff_violations=1, passages=3, invariants=passing, main_tae=3)
    assert late["value"] == 2 and not late["verified_on_parent"] and late["self_check"]["satisfied"]
    assert [c["verified"] for c in late["conditions"]] == [True, False, True, True]
    other = bounds.main_supply_b2(rows, {"f": [(900.0, 4)]}, {"f": [10.0]}, intended_vehicles=3, e1_init={"f": 1},
                                  ff_violations=0, passages=1, invariants=passing, main_tae=3)
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


def test_hotspot_weight_counts_only_the_route_group_it_localized(built):
    """A mixed-route row weighs at most its analyzed delayed vehicles, never the whole row's delayed_out."""
    profile = built[0]
    mixed = next(h for h in profile["hotspots"] if h["id"] == "H:LOC:edge:24483344#1")
    keys = ("row_id", "r", "delayed_out", "analyzed", "excluded", "weight")
    assert [tuple(row[key] for key in keys) for row in mixed["rows"]] == [
        ("R:0119Z5@5400", -15, 14, 12, 2, 12), ("R:0119Z4@5400", -14, 14, 10, 4, 10),
        ("R:0119Z4@9000", -3, 8, 7, 1, 3), ("R:0119Z4@10800", -2, 2, 1, 1, 1), ("R:0119Z5@7200", 4, 4, 4, 0, 0),
        ("R:0119Z4@6300", 11, 3, 2, 1, 0)]
    # Whole-row delayed_out would give 14 + 14 + 3 + 2 = 33 and let R:0119Z4@5400 claim its 4 unanalyzed vehicles.
    assert mixed["weight"] == 26 and mixed["lead"]["row_id"] == "R:0119Z5@5400"
    assert (mixed["lead"]["delayed_out"], mixed["lead"]["n_delayed"], mixed["lead"]["n_delayed_other_prefix"],
            mixed["lead"]["n_delayed_unpassed"]) == (14, 12, 2, 0)
    loc = {row["row_id"]: row for row in profile["localization"]["rows"]}
    assert (loc["R:0119Z4@5400"]["n_delayed"], loc["R:0119Z4@5400"]["n_delayed_other_prefix"]) == (10, 4)
    for hotspot in profile["hotspots"]:
        if hotspot["kind"] == "LOC":
            assert hotspot["weight"] == sum(row["weight"] for row in hotspot["rows"])
            for row in hotspot["rows"]:
                assert row["analyzed"] + row["excluded"] == row["delayed_out"]
                assert row["weight"] == min(max(-row["r"], 0), row["analyzed"])


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
    assert all(set(c) == {"id", "pass", "witness_ids", "reason"} and c["pass"] and c["witness_ids"] == []
               for c in checks)
    assert checks[0]["reason"] == ("7/7 goal inputs resolve to the goal's sha256; 8/8 run artifacts match the packet "
                                   "manifest or their byte reference; 8/8 equal their blob committed at HEAD")
    reason = checks[-1]["reason"]
    assert "field-counts.xml sha256 e9f90bb8 recorded, equals the profiled E1 output" in reason
    assert "instant-e1.xml sha256 21535d6e recorded, equals local-only-large-traces.json" in reason
    assert "local-only, its bytes are not in the repository and are not re-read" in reason
    assert "passages equal E1 nVehContrib on 182/182 scored rows" in reason
    assert "8589/8589 passages inside their recorded E1 interval at the step ceiling of time_s" in reason
    assert "8589/8589 within their vehicle's stay on the field edge in vehicle-routes.xml" in reason
    assert invariants.inv_inputs({"a": "1", "b": "2"}, {"a": "1", "b": "3"})["witness_ids"] == ["b"]
    assert invariants.inv_inputs({"a": "1"}, {"a": "1"}, {"cases/c/run.json": True, "cases/c/x.xml": False})[
        "witness_ids"] == ["manifest:cases/c/x.xml"]
    assert invariants.inv_inputs({"a": "1"}, {"a": "1"}, {"cases/c/run.json": True},
                                 {"benchmarks/p/cases/c/run.json.gz": False})["witness_ids"] == [
        "git:benchmarks/p/cases/c/run.json.gz"]
    loop = count_profile.FieldLoop("e_0", 10.0, 900.0)
    obs = invariants.inv_obs(goal_loops={"d": loop}, run_loops={"d": count_profile.FieldLoop("e_0", 9.0, 900.0)},
                             goal_attribute="nVehContrib", run_attributes=["nVehEntered"], goal_loops_sha256="x",
                             run_loops_sha256="y")
    assert obs.pop("reason") and obs == {
        "id": "INV-OBS", "pass": False, "witness_ids": ["attribute:nVehEntered", "detector:d", "file:field-loops.add.xml"]}
    hold = invariants.inv_hold(HOLD_NODES, {"run.json": HOLD_NODES[:3], "native-request.json": None})
    assert hold["witness_ids"] == ["missing:native-request.json", "node:LSA104_part0"]
    report = {"reconciled": False, "inputs_unchanged": True,
              "passages": [_unit_passage(vehicle) for vehicle in ("v", "v", "w")]}
    assert invariants.inv_pass(report, {"v": "d", "w": "e"}, e1_counts={}, bindings={}, **_UNIT_RUN)[
        "witness_ids"] == ["off_intention:w@d", "repeated:v@d", "report:reconciled"]
    one ={"reconciled": True, "inputs_unchanged": True,
           "inputs": [{"path": "C:\\run\\e1.xml", "sha256": "aa"}, {"path": "C:\\run\\extra.xml", "sha256": "cc"}],
           "passages": [_unit_passage("v")]}
    check = invariants.inv_pass(one, {"v": "d"}, e1_counts={("d", 0.0): 2, ("d", 900.0): 0},
                                bindings={"e1.xml": ("aa", "the E1"), "instant.xml": (None, "nowhere")}, **_UNIT_RUN)
    assert check["witness_ids"] == ["count:R:d@0", "report_input:extra.xml", "report_input:instant.xml"]
    assert "1/2 scored rows" in check["reason"] and "unexpected inputs extra.xml" in check["reason"]


_UNIT_RUN = {"routes": {vehicle: count_profile.Route(5.0, ("a", "x"), (8.0, 60.0)) for vehicle in ("v", "w")},
             "field_edges": {"d": "x"}, "e1_begin_s": 0.0, "e1_period_s": 900.0, "step_s": 0.1}


def _unit_passage(vehicle: str, **changes) -> dict:
    """A passage at 10.04 s on detector ``d`` (edge ``x``): notified at the step ceiling 10.1 s, in [0, 900)."""
    return {"vehicle_id": vehicle, "detector_id": "d", "time_s": 10.04, "notification_time_s": 10.1,
            "interval_begin_s": 0.0, "interval_end_s": 900.0, **changes}


@pytest.mark.parametrize(("changes", "witnesses"), [
    ({}, []),
    ({"time_s": 5010.04}, ["interval:v@d", "route:v@d"]),
    ({"time_s": 5010.04, "notification_time_s": 5010.1}, ["interval:v@d", "route:v@d"]),
    ({"time_s": 7.0}, ["interval:v@d", "route:v@d"]),
    ({"interval_begin_s": 0.0, "interval_end_s": 450.0}, ["interval:v@d"]),
    ({"interval_begin_s": 5.0, "interval_end_s": 905.0}, ["interval:v@d"]),
    ({"notification_time_s": None}, ["interval:v@d"]),
    ({"notification_time_s": 10.07}, ["interval:v@d"]),
    ({"time_s": 899.96, "notification_time_s": 899.99}, ["interval:v@d", "route:v@d"]),
    ({"vehicle_id": "u"}, ["route:u@d"]),
], ids=["genuine", "time-moved", "time-and-notification-moved", "before-entering-the-edge", "short-interval",
        "off-grid-interval", "no-notification", "notification-off-the-step-grid", "ceiling-in-the-next-interval",
        "vehicle-without-route"])
def test_inv_pass_binds_each_passage_to_its_interval_and_its_vehicle_route(changes, witnesses):
    """A passage must lie in its recorded E1 interval at the step ceiling of time_s and inside its vehicle's stay on
    the field edge (from entering it at 8.0 s to arriving at 60.0 s), within one step."""
    report = {"reconciled": True, "inputs_unchanged": True, "passages": [_unit_passage("v", **changes)]}
    intended = {"v": "d", "u": "d"}
    check = invariants.inv_pass(report, intended, e1_counts={}, bindings={}, **_UNIT_RUN)
    assert check["witness_ids"] == witnesses and check["pass"] is (not witnesses)
    edge_first = {**_UNIT_RUN, "routes": {"v": count_profile.Route(9.9, ("x",), (60.0,))}}
    assert invariants.inv_pass({**report, "passages": [_unit_passage("v")]}, intended, e1_counts={}, bindings={},
                               **edge_first)["pass"]


def _swapped(reader: PacketCase, relative: str, transform) -> PacketCase:
    """An in-memory reader whose ``relative`` run file holds ``transform(bytes)``; no file on disk changes.

    The swapped bytes carry their own raw sha256 while the tracked bytes, and so the manifest check, stay as they are:
    a run artifact that is not the one its passage report was reconciled against.
    """
    swapped = copy.copy(reader)
    key = reader.key(relative)

    def resolve_root(name: str):
        reference = reader.resolve_root(name)
        if name != key:
            return reference
        data = transform(reference.data)
        return dataclasses.replace(reference, data=data, sha256=hashlib.sha256(data).hexdigest())

    swapped.resolve_root = resolve_root
    return swapped


def _edit_report(edit, edited: dict | None = None):
    """A transform of the archived report; ``edited["vehicle"]`` records the vehicle id the edit returns, if any."""

    def transform(data: bytes) -> bytes:
        report = json.loads(gzip.decompress(data))
        vehicle = edit(report)
        if edited is not None:
            edited["vehicle"] = vehicle
        return gzip.compress(json.dumps(report).encode("utf-8"))

    return transform


def _profile_with(created, reader: PacketCase) -> dict:
    loaded, data, _ = created
    return count_profile.build_calibrate_profile(loaded, data, reader, run_dir="evaluation/native",
                                                 verifier={"commit": "0" * 40, "clean": True})[0]


def test_a_changed_e1_output_fails_inv_pass_blocks_the_profile_and_unverifies_bounds(created, reader):
    line = b'begin="3600.000" end="4500.000" id="hh_0119_Z_13_34018" nVehContrib="141"'

    def raise_z13(data: bytes) -> bytes:
        assert data.count(line) == 1
        return data.replace(line, line.replace(b'"141"', b'"151"'))

    profile = _profile_with(created, _swapped(reader, "evaluation/native/field-counts.xml", raise_z13))
    # The score reads the changed E1 output; the decomposition still reads the archived passages.
    assert (profile["score"]["tae_main"], profile["decomp"]["identity_tae"]) == (277, 275)
    checks = {check["id"]: check for check in profile["invariants"]}
    assert [name for name, check in checks.items() if not check["pass"]] == ["INV-PASS"]
    assert checks["INV-PASS"]["witness_ids"] == ["count:R:0119Z13@3600", "report_input:field-counts.xml"]
    assert "181/182 scored rows" in checks["INV-PASS"]["reason"]
    assert profile["status"] == "blocked" and profile["inputs"]["passages"] == "archived report, NOT reconciled"
    for name in ("B1_all182_request_floor", "B2_main_supply"):
        bound = profile["bounds"][name]
        assert not bound["verified_on_parent"] and bound["conditions"][-1] == {
            "condition": bounds.INVARIANTS_CONDITION, "verified": False, "evidence": "failed: INV-PASS"}
    assert [sol["value"] for sol in profile["bounds"]["SOL"]] == [None, None]
    compact_json, compact_text = render.render_compact({**profile, "details": {}})
    view = json.loads(compact_json)
    # The compact views give the witness total and the count per kind; the pairing witness leads the row witnesses.
    assert view["status"] == "blocked" and view["invariants"]["INV-PASS"] == {
        "n": 2, "by_kind": {"report_input": 1, "count": 1},
        "first": ["report_input:field-counts.xml", "count:R:0119Z13@3600"]}
    assert "PASS FAIL 2 (report_input 1, count 1): report_input:field-counts.xml,count:R:0119Z13@3600" in compact_text
    assert view["bounds"]["B2_main_supply"]["not_verified"] == [bounds.INVARIANTS_CONDITION]
    assert "verified_on_parent false" in compact_text and "NOT verified: INV-* on the candidate run" in compact_text


def _restate_input(name: str):
    def edit(report: dict) -> None:
        next(row for row in report["inputs"] if row["path"].endswith(name))["sha256"] = "0" * 64

    return edit


def _z13_passage(report: dict, begin_s: float) -> dict:
    return next(p for p in report["passages"] if p["detector_id"] == Z13 and p["interval_begin_s"] == begin_s)


def _move_one_passage(report: dict) -> str:
    passage = _z13_passage(report, 3600)
    passage.update(interval_begin_s=4500.0, interval_end_s=5400.0)
    return passage["vehicle_id"]


@pytest.mark.parametrize(("edit", "witnesses"), [
    (_restate_input("\\field-counts.xml"), ["report_input:field-counts.xml"]),
    (_restate_input("\\instant-e1.xml"), ["report_input:instant-e1.xml"]),
    # Its time_s and notification time stay in the old bin, so the passage also leaves its recorded interval.
    (_move_one_passage, ["count:R:0119Z13@3600", "count:R:0119Z13@4500", "interval:{vehicle}@" + Z13]),
], ids=["stale-e1-sha256", "other-instant-e1", "passage-in-another-bin"])
def test_a_report_not_paired_with_this_e1_output_fails_inv_pass(created, reader, edit, witnesses):
    """The report keeps reconciled and inputs_unchanged true; only the cross-artifact checks catch the pairing."""
    edited: dict = {}
    profile = _profile_with(created, _swapped(reader, goal.PARENT_PASSAGES, _edit_report(edit, edited)))
    check = profile["invariants"][-1]
    assert check["id"] == "INV-PASS" and profile["status"] == "blocked"
    assert check["witness_ids"] == [witness.format(**edited) for witness in witnesses]
    assert profile["score"]["tae_main"] == 275 and all(c["pass"] for c in profile["invariants"][:-1])
    assert not any(profile["bounds"][name]["verified_on_parent"]
                   for name in ("B1_all182_request_floor", "B2_main_supply"))


def test_a_run_artifact_off_the_packet_manifest_is_refused_before_profiling(created, reader):
    tracked = reader.key("evaluation/native/vehicle-routes.xml") + ".gz"
    assert tracked in reader.manifest
    tampered = copy.copy(reader)
    tampered.manifest = {**reader.manifest, tracked: "0" * 64}
    with pytest.raises(ValueError, match=r"INV-INPUTS failed for \['manifest:cases/h4_counter_z11_minus5_z5/"
                                         r"evaluation/native/vehicle-routes\.xml'\]"):
        _profile_with(created, tampered)
    assert reader.manifest[tracked] != "0" * 64


def _forged(reader: PacketCase, relative: str, transform, *, manifest: bool) -> tuple[PacketCase, str]:
    """An in-memory reader that reads ``relative`` the way a tampered checkout hands it over; no file on disk changes.

    The raw bytes are ``transform(bytes)``, gzipped back to the tracked form, with that form's own SHA-256 and git blob
    id. With ``manifest`` the packet manifest row is rewritten to match (a coherent forgery). Returns the reader and
    the repository-relative tracked name of the forged file.
    """
    forged = copy.copy(reader)
    key = reader.key(relative)
    original = reader.resolve_root(key)
    data = tracked = transform(original.data)
    for _ in range(original.tracked_path.name.count(".gz") - PurePosixPath(key).name.count(".gz")):
        tracked = gzip.compress(tracked, mtime=0)
    reference = dataclasses.replace(original, data=data, sha256=hashlib.sha256(data).hexdigest(),
                                    tracked_sha256=hashlib.sha256(tracked).hexdigest(),
                                    tracked_blob=git_blob_id(tracked))
    forged.resolve_root = lambda name: reference if name == key else reader.resolve_root(name)
    if manifest:
        row = original.tracked_path.resolve().relative_to(reader.packet_root).as_posix()
        forged.manifest = {**reader.manifest, row: reference.tracked_sha256}
        assert forged.pinned(reference) and not reader.pinned(reference)
    return forged, reader.tracked_name(original)


def _refusal(witnesses: list[str]) -> str:
    return f"profile refused: INV-INPUTS failed for {witnesses!r}"


def _swap_z13_vehicles(swapped_ids: dict):
    """Two Z13 passages in bins 3600 and 9000 swap vehicle ids: row counts hold, no repeat, no off-intention."""

    def edit(report: dict) -> None:
        first, later = _z13_passage(report, 3600), _z13_passage(report, 9000)
        first["vehicle_id"], later["vehicle_id"] = later["vehicle_id"], first["vehicle_id"]
        swapped_ids.update(first=first["vehicle_id"], later=later["vehicle_id"])

    return edit


def test_a_vehicle_id_swap_between_bins_fails_inv_pass_on_the_vehicle_routes(created, reader):
    """In memory the swap keeps every count, so the tracked vehicle routes catch it; on disk the bytes are not HEAD's."""
    swapped_ids: dict = {}
    profile = _profile_with(created, _swapped(reader, goal.PARENT_PASSAGES, _edit_report(_swap_z13_vehicles(
        swapped_ids))))
    check = profile["invariants"][-1]
    assert check["witness_ids"] == sorted(f"route:{vehicle}@{Z13}" for vehicle in swapped_ids.values())
    for text in ("182/182 scored rows", "8589/8589 passages inside their recorded E1 interval", "8587/8589 within"):
        assert text in check["reason"]
    assert profile["status"] == "blocked" and all(c["pass"] for c in profile["invariants"][:-1])
    assert not any(profile["bounds"][name]["verified_on_parent"]
                   for name in ("B1_all182_request_floor", "B2_main_supply"))
    assert [sol["value"] for sol in profile["bounds"]["SOL"]] == [None, None]
    # The same swap as a tampered checkout hands it over: refused before profiling, with or without its manifest row.
    for manifest, witnesses in ((False, ["git:{tracked}", f"manifest:{reader.key(goal.PARENT_PASSAGES)}"]),
                                (True, ["git:{tracked}"])):
        forged, tracked = _forged(reader, goal.PARENT_PASSAGES, _edit_report(_swap_z13_vehicles({})), manifest=manifest)
        with pytest.raises(ValueError) as refused:
            _profile_with(created, forged)
        assert str(refused.value) == _refusal([witness.format(tracked=tracked) for witness in witnesses])


@pytest.mark.parametrize("fields", [("time_s",), ("time_s", "notification_time_s")], ids=["time", "time+notification"])
def test_a_passage_moved_outside_its_interval_fails_inv_pass(created, reader, fields):
    """One Z13@3600 passage moved +5000 s with its bin fields unchanged: every count and flag still holds."""

    def move(report: dict) -> str:
        passage = _z13_passage(report, 3600)
        for name in fields:
            passage[name] += 5000.0
        return passage["vehicle_id"]

    edited: dict = {}
    profile = _profile_with(created, _swapped(reader, goal.PARENT_PASSAGES, _edit_report(move, edited)))
    check = profile["invariants"][-1]
    assert check["witness_ids"] == [f"interval:{edited['vehicle']}@{Z13}", f"route:{edited['vehicle']}@{Z13}"]
    for text in ("182/182 scored rows", "8588/8589 passages inside their recorded E1 interval", "8588/8589 within"):
        assert text in check["reason"]
    assert profile["status"] == "blocked" and all(c["pass"] for c in profile["invariants"][:-1])
    assert not any(profile["bounds"][name]["verified_on_parent"]
                   for name in ("B1_all182_request_floor", "B2_main_supply"))


def _earlier_insertions(vehicles: set[str], requests: dict):
    """vehicle-routes.xml with each named vehicle's recorded insertion 100 s earlier (never before its request)."""
    pattern = re.compile(rb'(<vehicle id=")([^"]+)("[^>]*?\bdepart=")([0-9.]+)(")')

    def transform(data: bytes) -> bytes:
        def edit(match: re.Match) -> bytes:
            vehicle = match[2].decode()
            if vehicle not in vehicles:
                return match[0]
            depart = max(requests[vehicle].depart_s, float(match[4]) - 100.0)
            return match[1] + match[2] + match[3] + f"{depart:.2f}".encode() + match[5]

        forged, edits = pattern.subn(edit, data)
        assert edits >= len(vehicles) and forged != data
        return forged

    return transform


def _inject_braking_warning(data: bytes) -> bytes:
    command = json.loads(data)
    command["stderr"] += ("Warning: Vehicle 'od_0000_v00000' performs emergency braking on lane '44532531_0' with "
                          "decel=9.00, wished=4.50, severity=1.00, time=100.00.\n")
    return json.dumps(command).encode("utf-8")


@pytest.mark.parametrize("artifact", ["vehicle-routes.xml", "command.json"])
def test_a_coherent_forgery_of_a_run_artifact_and_its_manifest_row_is_refused(created, reader, production_inputs,
                                                                             artifact):
    """The manifest is a packet file, so an artifact edited together with its manifest row passes the manifest; the
    blob committed at HEAD is the trust root outside the packet that refuses it."""
    inputs = production_inputs
    if artifact == "vehicle-routes.xml":
        measured = {p["vehicle_id"]: p["interval_begin_s"] for p in inputs["passages"] if p["detector_id"] == Z13}
        delayed = {vehicle for vehicle, field in inputs["intended"].items() if field == Z13
                   and count_profile._bin_begin(inputs["arrivals"][vehicle], 0.0, 900.0) == 3600.0
                   and measured.get(vehicle) != 3600.0}
        assert len(delayed) == 36
        transform = _earlier_insertions(delayed, inputs["requests"])
    else:
        transform = _inject_braking_warning
    forged, tracked = _forged(reader, f"evaluation/native/{artifact}", transform, manifest=True)
    # Trusting the manifest alone, the forgery would be measured with every invariant passing and changed numbers.
    trusting = copy.copy(forged)
    trusting.committed = lambda references: {reader.tracked_name(r): True for r in references}
    accepted = _profile_with(created, trusting)
    assert accepted["status"] == "measured" and all(check["pass"] for check in accepted["invariants"])
    if artifact == "vehicle-routes.xml":
        # The parent localizes R:0119Z13@3600 at insertion +185.1 s and weighs its hotspot 58.
        z13 = next(row for row in accepted["localization"]["rows"] if row["row_id"] == "R:0119Z13@3600")
        assert [(a["anchor"], a["diff"]) for a in z13["coanchors"]] == [("44532531", 103.7), ("insertion", 85.1)]
        assert accepted["hotspots"][0]["weight"] == 54
    else:
        assert accepted["integrity"]["emergency_braking"]["count"] == 6
    with pytest.raises(ValueError) as refused:
        _profile_with(created, forged)
    assert str(refused.value) == _refusal([f"git:{tracked}"])


def _corrupt_packet(root: Path) -> Path:
    packet = root / "benchmarks" / "packet"
    (packet / "cases" / "c").mkdir(parents=True)
    (packet / "byte-references.json").write_text(json.dumps({"aliases": {}}), encoding="utf-8")
    whole = gzip.compress(b"<additional/>", mtime=0)
    (packet / "cases" / "c" / "not-gzip.xml.gz").write_bytes(b"not a gzip file")
    (packet / "cases" / "c" / "truncated.xml.gz").write_bytes(whole[:12])
    (packet / "cases" / "c" / "corrupt.xml.gz").write_bytes(whole[:10] + b"\xff" * 16)
    return packet


@pytest.mark.parametrize(("name", "error"), [("not-gzip", gzip.BadGzipFile), ("truncated", EOFError),
                                             ("corrupt", zlib.error)])
def test_an_undecodable_run_artifact_is_a_named_manifest_witness(created, reader, tmp_path, name, error):
    """A run artifact whose gzip layer does not decode is refused through INV-INPUTS, not with a raw decode error."""
    packet = _corrupt_packet(tmp_path)
    with pytest.raises(error):
        resolve_packet_reference(packet, f"cases/c/{name}.xml")
    key = reader.key("evaluation/native/e1.add.xml")
    broken = copy.copy(reader)
    broken.resolve_root = lambda relative: (resolve_packet_reference(packet, f"cases/c/{name}.xml") if relative == key
                                            else reader.resolve_root(relative))
    with pytest.raises(ValueError) as refused:
        _profile_with(created, broken)
    assert str(refused.value) == _refusal([f"manifest:{key}"])
    # A goal input whose tracked target does not decode is named the same way, by its goal alias.
    prepared = copy.copy(reader)
    prepared.resolve = lambda relative: (resolve_packet_reference(packet, f"cases/c/{name}.xml")
                                         if relative == "prepared/field-loops.add.xml" else reader.resolve(relative))
    with pytest.raises(ValueError) as refused:
        _profile_with(created, prepared)
    assert str(refused.value) == _refusal(["field-loops.add.xml"])


def test_committed_compares_the_tracked_bytes_read_with_the_blob_at_head(reader, tmp_path):
    references = [reader.resolve_root(reader.key("evaluation/native/run.json")), reader.resolve_root(LOCAL_ONLY_FILE)]
    names = [reader.tracked_name(reference) for reference in references]
    assert reader.committed(references) == dict.fromkeys(names, True)
    hashed = subprocess.run(["git", "-C", str(ROOT), "hash-object", "--no-filters", str(references[0].tracked_path)],
                            capture_output=True, encoding="utf-8", timeout=60).stdout.strip()
    assert references[0].tracked_blob == git_blob_id(references[0].tracked_path.read_bytes()) == hashed
    other = dataclasses.replace(references[0], tracked_blob=git_blob_id(b"other bytes"))
    assert reader.committed([other]) == {names[0]: False} and head_blob_ids(ROOT, []) == {}
    packet = tmp_path / "benchmarks" / "packet"
    (packet / "cases" / "c").mkdir(parents=True)
    (packet / "byte-references.json").write_text(json.dumps({"aliases": {}}), encoding="utf-8")
    (packet / "cases" / "c" / "run.json.gz").write_bytes(gzip.compress(b"{}"))
    outside = PacketCase(packet, "c")
    assert outside.committed([outside.resolve("run.json")]) == {"benchmarks/packet/cases/c/run.json.gz": False}


def test_compact_views_count_failing_witnesses_by_kind(created, reader):
    """Every nVehContrib +1: 182 row witnesses and one pairing witness; both views give the total and every kind."""

    def plus_one(data: bytes) -> bytes:
        return re.sub(rb'nVehContrib="(\d+)"', lambda match: b'nVehContrib="%d"' % (int(match[1]) + 1), data)

    profile = _profile_with(created, _swapped(reader, "evaluation/native/field-counts.xml", plus_one))
    check = profile["invariants"][-1]
    assert len(check["witness_ids"]) == 183 and "0/182 scored rows" in check["reason"]
    compact_json, compact_text = render.render_compact({**profile, "details": {}})
    assert len(compact_json) <= render.CAP_CHARACTERS and len(compact_text) <= render.CAP_CHARACTERS
    shown = json.loads(compact_json)["invariants"]["INV-PASS"]
    assert shown == {"n": 183, "by_kind": {"report_input": 1, "count": 182},
                     "first": ["report_input:field-counts.xml", *check["witness_ids"][:4]]}
    assert ("PASS FAIL 183 (report_input 1, count 182): report_input:field-counts.xml,count:R:0119Z11@0,"
            "count:R:0119Z11@10800 +180 more") in compact_text
    witnesses = sorted(["count:R:a@0", "count:R:b@0", "off_intention:v@d", "repeated:w@d", "repeated:x@d",
                        "report:reconciled", "report_input:x", "zeta:1", "bare"])
    view = render._witnesses(witnesses, 5, False)
    assert view == {"n": 9, "by_kind": {"report": 1, "report_input": 1, "repeated": 2, "off_intention": 1, "id": 1,
                                        "zeta": 1, "count": 2},
                    "first": ["report:reconciled", "report_input:x", "repeated:w@d", "off_intention:v@d", "bare"]}
    assert render._witness_text(render._witnesses(witnesses, 3, False)).endswith(
        ": report:reconciled,report_input:x,repeated:w@d +6 more")
    many = render._witnesses([f"k{n}:w" for n in range(render.CLIP_ITEMS + 2)], 3, True)
    assert len(many["by_kind"]) == render.CLIP_ITEMS + 1 and many["by_kind"]["+2 kinds"] == 2


def test_compact_views_are_capped_and_list_top_hotspots(built):
    profile = {**built[0], "details": {}}
    compact_json, compact_text = render.render_compact(profile)
    assert len(compact_json) <= render.CAP_CHARACTERS and len(compact_text) <= render.CAP_CHARACTERS
    view = json.loads(compact_json)
    assert view["view"] == "compact" and view["status"] == "measured" and len(view["hot"]) == 5
    assert (view["goal"], view["cand"], view["verifier"]) == (f"{GOAL_ID}@2ba4c172", "c-4f35cd291717", "0" * 40)
    assert [(hotspot["id"], hotspot["w"]) for hotspot in view["hot"]] == [
        ("H:LOC:entry:44532531", 58), ("H:LOC:edge:24483344#1", 26), ("H:LOC:co:27d1dde6", 22),
        ("H:LOC:edge:24483344#0", 21), ("H:BAL:field:0119Z13", 13)]
    assert len(view["hot"][0]["rows"]) == 7 and view["hot"][0]["rows"]["more"] == "+21 more"
    assert view["hot_more"] == "+10 more" and "R:0535Z4@10800" in view["hot"][2]["rows"]
    assert view["hot"][1]["rows"]["R:0119Z4@5400"] == "r-14 y55 p55 m41 41/0/14 n10/29 x4"
    # "x" / "not analyzed" counts delayed_out vehicles left out; the peer clause counts on-time peers on another
    # route prefix and never reuses the word "excluded".
    assert view["hot"][1]["peers"] == ("12 analyzed vs 26 on-time; entry 963776077#0; 8-edge prefix; other-prefix "
                                       "peers 5 on 3 entries")
    assert view["hot"][2]["peers"].endswith("; other-prefix peers 28 on 771709146#0")
    assert not any("excluded" in hotspot.get("peers", "") for hotspot in view["hot"])
    assert "TAE 275" in compact_text and compact_text.count("\nHOT") == 6
    assert "R:0119Z4@5400 r-14 dout 14 w 10 (4 not analyzed)" in compact_text
    assert "other-prefix peers 5 on 3 entries" in compact_text and "excluded" not in compact_text
    # Both views show each bound with its conditions and their verification, from one shared presentation.
    bound_lines = [line for line in compact_text.splitlines() if line.startswith("BOUND")]
    assert len(bound_lines) == 3 and bound_lines[2].startswith("BOUND  SOL TAE_all182 0.0, TAE_main130 0.0")
    for line, name in zip(bound_lines, ("B1_all182_request_floor", "B2_main_supply")):
        shown = view["bounds"][name]
        assert shown["verified_on_parent"] is True and "not_verified" not in shown
        assert "verified_on_parent true | applies if: " + "; ".join(shown["applies_if"]) in line
        assert shown["applies_if"][-1] == bounds.INVARIANTS_CONDITION
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


def test_cli_goal_create_and_profile(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "dont_write_bytecode", sys.dont_write_bytecode)
    spec = importlib.util.spec_from_file_location("astra_twin_cli", ROOT / "plugins/astra-sumo/scripts/astra_twin.py")
    cli = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cli)
    assert Path(sys.path[0]) == cli.SRC_ROOT and sys.path.count(str(cli.SRC_ROOT)) == 1
    assert sys.dont_write_bytecode is True
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
    scratch.mkdir()
    report = doctor.run_doctor(ROOT, forbidden_paths=[str(ROOT / "no-such-src")], expected_commit="0" * 40,
                               scratch_root=scratch)
    checks = report["checks"]
    assert report["status"] == "blocked" and not report["pass"] and checks["head_matches_expected"] is False
    assert checks["astra_sumo_in_process_under_clone"] and checks["astra_sumo_child_under_clone"]
    assert checks["forbidden_absent_from_child_sys_path"] and checks["no_foreign_astra_sumo_on_child_sys_path"]
    assert report["clone"]["commit"] and report["clone"]["expected_commit_source"] == "--expect-commit"
    assert Path(report["astra_sumo"]["child_cwd"]).resolve().parent == scratch.resolve()
    assert list(scratch.iterdir()) == [] and report["astra_sumo"]["child_scratch_source"] == "--scratch-root"
    assert report["astra_sumo"]["child_command"] == ["-B", "-m", "astra_sumo.twin.doctor"]
    head = report["clone"]["commit"]
    assert doctor.commit_from_directory(tmp_path / head) == head
    assert doctor.commit_from_directory(tmp_path / "astra-impl") is None
    if report["sumo"]["binary"] is None:
        pytest.skip("SUMO binary not installed; version check not exercised")
    assert report["sumo"]["version"] == doctor.EXPECTED_SUMO and checks["libsumo_import"]
    assert checks["sumo_distributions"] and checks["sumo_binary_in_venv"]


def test_doctor_scratch_defaults_to_the_clone_outputs_never_the_system_temp(tmp_path, monkeypatch):
    system_temp = tmp_path / "system-temp"
    system_temp.mkdir()
    monkeypatch.setattr(doctor.tempfile, "tempdir", str(system_temp))
    outputs = ROOT / doctor.DEFAULT_SCRATCH
    existed = outputs.is_dir()
    before = sorted(outputs.iterdir()) if existed else None
    report = doctor.run_doctor(ROOT, expected_commit="0" * 40)
    source = report["astra_sumo"]
    assert source["child_scratch_root"] == str(outputs) and source["child_scratch_source"].endswith("outputs/")
    assert Path(source["child_cwd"]).resolve().parent == outputs.resolve(), source["child_error"]
    assert list(system_temp.iterdir()) == []
    assert (sorted(outputs.iterdir()) == before) if existed else not outputs.exists()
    assert subprocess.run(["git", "-C", str(ROOT), "check-ignore", "-q", f"{doctor.DEFAULT_SCRATCH}/x"],
                          capture_output=True).returncode == 0
