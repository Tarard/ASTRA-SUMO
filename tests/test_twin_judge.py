"""M2 judge harness: keyed perturbations, the cell_depart_shift op, cache identity and canonical measurement parity.

No test starts SUMO. A fake native runner replays the tracked parent run into a new directory with other paths,
another generation header and other CPU durations, so canonical parity is checked against real outputs.
"""

from __future__ import annotations

import hashlib
import inspect
import json
import re
import xml.etree.ElementTree as ET
from decimal import Decimal
from pathlib import Path

import pytest

from astra_sumo.core.detector_demand_replay import run_point_detector_demand
from astra_sumo.twin import goal, noise, perturb, runner, scenario_ops

ROOT = Path(__file__).resolve().parents[1]
PACKET = "benchmarks/hamburg_ring1_chronological_od_feedback_20261024_v1"
CASE = "h4_counter_z11_minus5_z5"


@pytest.fixture(scope="module")
def reader():
    return goal.case_reader(ROOT, PACKET, CASE)


@pytest.fixture(scope="module")
def prepared(reader) -> dict[str, bytes]:
    return {name: reader.read(f"prepared/{name}") for name in goal.PREPARED_FILES}


@pytest.fixture(scope="module")
def demand(prepared) -> perturb.Demand:
    return perturb.parse_demand(prepared["demand.rou.xml"])


@pytest.fixture(scope="module")
def goal_file(tmp_path_factory) -> Path:
    path, _ = goal.write_goal(goal.create_calibrate_goal(ROOT, PACKET, CASE), tmp_path_factory.mktemp("store"))
    return path


@pytest.fixture(scope="module")
def baseline(reader) -> dict:
    return runner.baseline_measurement(reader)


def _vehicles(data: bytes) -> dict[str, ET.Element]:
    return {row.get("id"): row for row in ET.fromstring(data).findall("vehicle")}


def _without_depart(element: ET.Element) -> bytes:
    copy = ET.fromstring(ET.tostring(element))
    copy.attrib.pop("depart")
    return ET.tostring(copy)


def test_demand_split_reproduces_the_tracked_bytes(prepared, demand, goal_file):
    assert demand.to_bytes() == prepared["demand.rou.xml"] and len(demand.vehicles) == 8589
    assert demand.sha256 == goal.load_goal(goal_file)[0].inputs.files["demand.rou.xml"]


def test_jitter_is_keyed_bounded_and_changes_only_depart_tokens(prepared, demand):
    key = perturb.DrawKey.public_key("k1")
    first, record = perturb.apply_jitter(demand, key)
    again, _ = perturb.apply_jitter(demand, key)
    other, _ = perturb.apply_jitter(demand, perturb.DrawKey.public_key("k2"))
    assert first.to_bytes() == again.to_bytes() != other.to_bytes()
    before, after = _vehicles(prepared["demand.rou.xml"]), _vehicles(first.to_bytes())
    assert before.keys() == after.keys()
    offsets = [Decimal(after[v].get("depart")) - Decimal(before[v].get("depart")) for v in before]
    assert all(Decimal("-0.5") <= value < Decimal("0.5") for value in offsets)
    assert all(_without_depart(before[v]) == _without_depart(after[v]) for v in before)
    departs = [v.depart for v in first.vehicles]
    assert departs == sorted(departs) and record["key"]["public"] and record["base_sha256"] == demand.sha256
    assert record["key"]["key_sha256"] == key.fingerprint and key.key.hex() not in json.dumps(record)


def test_jitter_offsets_depend_on_the_vehicle_id_only(prepared, demand):
    """Common random numbers: removing one cell leaves every other vehicle's jittered depart token unchanged."""
    cells = scenario_ops.od_cells(prepared["lane-intentions.json"])
    subset = perturb.Demand(demand.head, tuple(v for v in demand.vehicles if cells[v.vehicle_id] != 0),
                            demand.separator, demand.tail)
    key = perturb.DrawKey.from_hex("panel", "00ff" * 16)
    full, _ = perturb.apply_jitter(demand, key)
    part, _ = perturb.apply_jitter(subset, key)
    tokens = {v.vehicle_id: v.token for v in full.vehicles}
    assert all(tokens[v.vehicle_id] == v.token for v in part.vehicles)


def test_tie_order_permutes_only_equal_departures(demand):
    result, record = perturb.apply_tie_order(demand, perturb.DrawKey.public_key("o1"))
    assert sorted(v.data for v in result.vehicles) == sorted(v.data for v in demand.vehicles)
    assert [v.depart for v in result.vehicles] == [v.depart for v in demand.vehicles]
    assert record["tie_groups"] == 180 and record["tied_vehicles"] == 368 and 0 < record["groups_reordered"] < 180


@pytest.mark.parametrize("text, message", [
    (b'<routes><flow id="f" begin="0" end="9" number="2"/>'
     b'<vehicle id="a" depart="1.0"><route edges="x"/></vehicle></routes>', "Only a header"),
    (b'<routes><vehicle id="a" depart="triggered"><route edges="x"/></vehicle></routes>', "plain decimal"),
    (b'<routes><vehicle id="a" depart="1"><route edges="x"/></vehicle>\n'
     b'<vehicle id="b" depart="2"><route edges="x"/></vehicle><vehicle id="c" depart="3"><route edges="x"/>'
     b'</vehicle></routes>', "uniform whitespace"),
])
def test_unsupported_demand_forms_are_refused(text, message):
    with pytest.raises(perturb.DemandFormatError, match=message):
        perturb.parse_demand(text)


def test_cell_depart_shift_moves_one_cell_and_keeps_survivors_byte_identical(prepared, demand):
    intentions = prepared["lane-intentions.json"]
    shifted, record = scenario_ops.cell_depart_shift(demand, intentions, cell=0, shift_s=900, horizon_s=14400,
                                                     od_pairs=22)
    again, _ = scenario_ops.cell_depart_shift(demand, intentions, cell=0, shift_s=900, horizon_s=14400, od_pairs=22)
    assert shifted.to_bytes() == again.to_bytes()
    cells = scenario_ops.od_cells(intentions)
    before, after = demand.by_id(), shifted.by_id()
    moved = [v for v in before if cells[v] == 0]
    assert len(moved) == record["vehicles"] == 13 and record["pair"] == 0 and record["period"] == 0
    assert all(after[v].depart - before[v].depart == 900 for v in moved)
    assert all(after[v].data == before[v].data for v in before if cells[v] != 0)
    assert record["survivors_byte_identical_in_order"] and record["fields"] == ["hh_0535_Z_8_34287"]
    with pytest.raises(ValueError, match="outside"):
        scenario_ops.cell_depart_shift(demand, intentions, cell=0, shift_s=-200, horizon_s=14400)
    with pytest.raises(ValueError, match="no vehicles"):
        scenario_ops.cell_depart_shift(demand, intentions, cell=21, shift_s=300)
    assert scenario_ops.parse_op("cell_depart_shift:12:-300") == ("cell_depart_shift", {"cell": 12, "shift_s": "-300"})


def test_cell_table_checks_the_period_major_layout(prepared, demand):
    table = scenario_ops.cell_table(demand, prepared["lane-intentions.json"], od_pairs=22, periods=14, period_s=900)
    assert len(table) == 295 and table[22]["pair"] == 0 and table[22]["period"] == 1
    single = [row for row in table.values() if len(row["fields"]) == 1 and row["vehicles"] >= 10]
    assert len(single) == 114
    with pytest.raises(ValueError, match="period"):
        scenario_ops.cell_table(demand, prepared["lane-intentions.json"], od_pairs=21, periods=15, period_s=900)


def test_the_judge_mirrors_the_shared_driver_call(reader, goal_file):
    driver = runner.shared_driver_call(reader)
    loaded, _ = goal.load_goal(goal_file)
    assert driver["line"] == 100 and runner.check_driver(loaded.run, driver) == []
    changed = loaded.run.model_copy(update={"seed": 11, "backend": "traci"})
    problems = runner.check_driver(changed, driver)
    assert any("seed=104" in p for p in problems) and any("backend" in p for p in problems)


def test_every_result_relevant_argument_enters_the_cache_key(goal_file, reader):
    """Each run_point_detector_demand parameter is in the request: as an argument, an input hash or the binary hash."""
    loaded, _ = goal.load_goal(goal_file)
    driver = runner.shared_driver_call(reader)
    runtime = {"sumo_binary_sha256": "s", "libsumo_library_sha256": "l", "verifier_commit": "c", "_clean": False}
    base = dict(seed=104, inputs_sha256={k: k for k in runner.INPUT_KEYWORDS}, scenario_op=None, perturbation=None,
                base_demand_sha256="d", driver=driver, runtime=runtime)
    request = runner.effective_request(loaded.run, **base)
    covered = set(request["arguments"]) | set(request["inputs_sha256"]) | {"sumo_binary", "output_dir"}
    assert set(inspect.signature(run_point_detector_demand).parameters) == covered
    key = runner.digest(request)
    variants = [loaded.run.model_copy(update={"junction_body_hold_nodes": ["14398547", "205069138", "18653534"]}),
                loaded.run.model_copy(update={"backend": "traci"}),
                loaded.run.model_copy(update={"all_stopped_timeout_s": 600.0})]
    assert all(runner.digest(runner.effective_request(run, **base)) != key for run in variants)
    keyed = {**base, "perturbation": {"family": "depart_jitter", "key": {"key_sha256": "x"}}}
    assert runner.digest(runner.effective_request(loaded.run, **{**base, "seed": 11})) != key
    assert runner.digest(runner.effective_request(loaded.run, **keyed)) != key
    assert runner.digest(runner.effective_request(loaded.run, **{**base, "runtime": {**runtime, "_clean": True}})) == key
    assert runner.digest(runner.effective_request(
        loaded.run, **{**base, "runtime": {**runtime, "libsumo_library_sha256": "other"}})) != key


def test_plans_in_different_stores_share_one_key(goal_file, tmp_path):
    runtime = runner.runtime_identity(ROOT)
    a = runner.plan_judge(goal_file, tmp_path / "a", label="a", purpose="t", repository_root=ROOT, runtime=runtime)
    b = runner.plan_judge(goal_file, tmp_path / "b", label="b", purpose="t", repository_root=ROOT, runtime=runtime)
    k = runner.plan_judge(goal_file, tmp_path / "a", label="k", purpose="t", repository_root=ROOT, runtime=runtime,
                          draw=perturb.DrawKey.public_key("k1"))
    assert a.key == b.key != k.key and a.run_dir != b.run_dir
    assert a.request["inputs_sha256"]["route_file"] == goal.load_goal(goal_file)[0].inputs.files["demand.rou.xml"]
    assert k.provenance["perturbation"]["sha256"] == k.request["inputs_sha256"]["route_file"]
    with pytest.raises(ValueError, match="draw key"):
        runner.plan_judge(goal_file, tmp_path / "a", label="x", purpose="t", repository_root=ROOT, runtime=runtime,
                          family="tie_order")


def _instant_e1(report: dict, mapping: dict[str, str]) -> bytes:
    trace = {periodic: instant for instant, periodic in mapping.items()}
    root = ET.Element("instantE1")
    for row in report["passages"]:
        ET.SubElement(root, "instantOut", id=trace[row["detector_id"]], time=repr(row["time_s"]), state="leave",
                      vehID=row["vehicle_id"], occupancy=repr(row["occupancy_s"]))
    for row in report["early_departures"]:
        ET.SubElement(root, "instantOut", id=trace[row["detector_id"]], time=repr(row["time_s"]), state="leave",
                      vehID=row["vehicle_id"])
    return ET.tostring(root)


def fake_runner(reader):
    """Replays the tracked parent outputs with another header, other paths and other CPU durations."""
    directory = goal.BASELINE_RUN_DIR
    run = reader.read_json(f"{directory}/run.json")
    calls = []

    def replay(**kwargs) -> dict:
        calls.append(kwargs)
        out = Path(kwargs["output_dir"])
        out.mkdir(parents=True)
        counts = reader.read(f"{directory}/field-counts.xml").replace(b"2026-10-24T09:52:58", b"2030-01-01T00:00:00")
        (out / "field-counts.xml").write_bytes(counts.replace(b"C:\\Users\\Danial", b"D:\\elsewhere"))
        summary = re.sub(rb' duration="\d+"', b' duration="7"', reader.read(f"{directory}/summary.xml"))
        (out / "summary.xml").write_bytes(summary)
        for name in ("tripinfo.xml", "vehicle-routes.xml", "command.json", "stall-monitor.json"):
            (out / name).write_bytes(reader.read(f"{directory}/{name}"))
        (out / "instant-e1.xml").write_bytes(_instant_e1(runner.passage_report(reader.read(goal.PARENT_PASSAGES)),
                                                         run["instant_to_periodic_ids"]))
        (out / "run.json").write_text(json.dumps(run), encoding="utf-8")
        return run

    return replay, calls


def test_canonical_measurement_ignores_headers_paths_and_cpu_durations(reader, baseline, goal_file, tmp_path):
    replay, _ = fake_runner(reader)
    replay(output_dir=tmp_path / "native")
    raw = (tmp_path / "native/field-counts.xml").read_bytes()
    assert raw != reader.read(f"{goal.BASELINE_RUN_DIR}/field-counts.xml")
    measured, passages = runner.measure_run(tmp_path / "native", step_length=0.1)
    assert passages["reconciled"] and len(passages["passages"]) == 8589
    comparison = runner.compare_measurements(baseline, measured)
    assert all(comparison["levels"].values()) and not comparison["e1_rows_differing"]
    assert comparison["vehicles_changing_bin"] == 0 and len(comparison["levels"]) == 11
    edited = json.loads(json.dumps(measured))
    edited["e1_counts"][0][3] += 1
    edited["digests"] = runner.measurement_digests(edited)
    assert not runner.compare_measurements(baseline, edited)["levels"]["e1_counts"]
    score, rows = runner.score_measurement(baseline, reader.read_csv("prepared/expected-counts.csv"),
                                           reader.read_csv("prepared/initialization-counts.csv"))
    assert (score["tae_main"], score["init52_tae"], score["all182_tae"], len(rows)) == (275, 97, 372, 182)


def test_a_truncated_trace_is_named_not_raised():
    digest = runner.trace_digest("tripinfo.xml", b'<tripinfos><tripinfo id="a" duration="1"/><tripinfo id="b"')
    assert digest["sha256"] is None and digest["rows"] == 1 and "error" in digest


def test_execute_resume_and_cache_never_repeat_a_native_run(reader, baseline, goal_file, tmp_path):
    replay, calls = fake_runner(reader)
    plan = runner.plan_judge(goal_file, tmp_path / "store", label="P1", purpose="test", repository_root=ROOT)
    main = reader.read_csv("prepared/expected-counts.csv")
    init = reader.read_csv("prepared/initialization-counts.csv")
    result = runner.execute_plan(plan, main_rows=main, init_rows=init, runner=replay, watch_memory=False)
    assert result["score"]["tae_main"] == 275 and result["native_run"] and len(calls) == 1
    assert result["digests"] == baseline["digests"]
    assert calls[0]["junction_body_hold_nodes"] == ("14398547", "205069138", "18653534", "LSA104_part0")
    assert calls[0]["seed"] == 104 and calls[0]["backend"] == "libsumo" and calls[0]["timeout_seconds"] == 4800
    (Path(plan.run_dir) / "judge.json").unlink()
    resumed = runner.execute_plan(plan, main_rows=main, init_rows=init, runner=replay, watch_memory=False)
    assert len(calls) == 1 and not resumed["native_run"] and resumed["digests"] == result["digests"]
    ledger = tmp_path / "runs.jsonl"
    cached = runner.judge_many([plan], store=tmp_path / "store", ledger=ledger)
    assert cached[0]["cache"] == "hit" and not ledger.exists()


def test_panel_spread_statistics():
    def judged(tae, rows):
        return {"score": {"tae_main": tae, "init52_tae": 10, "all182_tae": tae + 10, "sse": 1, "max": 1, "exact": 1},
                "rows": {name: {"measured": m, "diff": m - 5} for name, m in rows.items()}}

    reference = judged(20, {"a": 5, "b": 7, "i": 4})
    draws = [judged(22, {"a": 5, "b": 9, "i": 4}), judged(19, {"a": 6, "b": 7, "i": 4})]
    spread = noise.score_spread(reference, draws)
    assert spread["tae_main"]["differences"] == [2, -1] and spread["tae_main"]["max_abs_difference"] == 2
    rows = noise.row_spread(reference, draws, init_rows=frozenset({"i"}))
    assert rows["changed_vs_reference"] == {"all": 2, "main": 2, "init": 0} and rows["max_count_move"] == 2
    margins = noise.provisional_margins(reference, draws)
    assert margins["g_panel_term"] == 3 and margins["m_row"] == 3 and margins["m_init"] == 2
    assert noise.t_upper_bound([0.0, 0.0], 2.353) == 0.0
    base = {"passages": {"full": [["a", "d1", 0.0, 10.0], ["b", "d1", 0.0, 20.0]], "early_departures": []}}
    draw = {"passages": {"full": [["a", "d1", 900.0, 910.0], ["b", "d1", 0.0, 20.25]], "early_departures": []}}
    moved = noise.passage_spread(base, [draw])["per_draw"][0]
    assert moved["bin_changed"] == 1 and moved["detector_changed"] == 0 and moved["time_shift_s"]["max"] == 900.0
    assert moved["time_shift_s"]["share_le_0.5"] == 0.5


# ---- M4 candidate-network scenarios --------------------------------------------------------------------------------

PILOT = Path(r"E:\twin-store\m2-pilot")


def _candidate_manifest(tmp_path: Path, prepared: dict[str, bytes]) -> tuple[dict, Path]:
    folder = tmp_path / "candidate"
    folder.mkdir()
    files = {"net": ("network.net.xml", prepared["network.net.xml"] + b"<!-- candidate -->\n"),
             "control": ("control.add.xml", prepared["control.add.xml"] + b"<!-- candidate -->\n")}
    manifest = {"id": "F-test", "goal": {"path": "goal.json", "sha256": "0" * 64},
                "parent": {"net_sha256": hashlib.sha256(prepared["network.net.xml"]).hexdigest(),
                           "control_sha256": hashlib.sha256(prepared["control.add.xml"]).hexdigest()}}
    for name, (filename, data) in files.items():
        (folder / filename).write_bytes(data)
        manifest[name] = {"path": filename, "sha256": hashlib.sha256(data).hexdigest()}
    return manifest, folder


def test_a_candidate_scenario_replaces_only_the_net_and_control(goal_file, prepared, tmp_path):
    runtime = runner.runtime_identity(ROOT)
    manifest, folder = _candidate_manifest(tmp_path, prepared)
    candidate = runner.candidate_scenario(manifest, base=folder)
    draw = perturb.DrawKey.public_key("fragility/F-test/k1")
    parent = runner.plan_judge(goal_file, tmp_path / "store", label="p", purpose="t", repository_root=ROOT,
                               runtime=runtime, family="depart_jitter", draw=draw)
    plan = runner.plan_judge(goal_file, tmp_path / "store", label="c", purpose="t", repository_root=ROOT,
                             runtime=runtime, family="depart_jitter", draw=draw, candidate=candidate)
    assert "candidate" not in parent.request and plan.key != parent.key
    assert {key for key in plan.request if plan.request[key] != parent.request.get(key)} == {"inputs_sha256",
                                                                                              "candidate"}
    changed = {key for key, value in plan.request["inputs_sha256"].items()
               if value != parent.request["inputs_sha256"][key]}
    assert changed == {"net_file", "signal_program_file"}
    files = goal.load_goal(goal_file)[0].inputs.files
    assert plan.request["candidate"] == {
        "net_file": {"parent_sha256": files["network.net.xml"], "candidate_sha256": manifest["net"]["sha256"]},
        "signal_program_file": {"parent_sha256": files["control.add.xml"],
                                "candidate_sha256": manifest["control"]["sha256"]}}
    assert hashlib.sha256(Path(plan.staged["net_file"]).read_bytes()).hexdigest() == manifest["net"]["sha256"]
    assert plan.provenance["candidate"]["id"] == "F-test" and plan.provenance["candidate"]["goal"]["sha256"] == "0" * 64
    line = runner.ledger_line(plan, None)
    assert line["candidate"] == {"id": "F-test", "net_file": manifest["net"]["sha256"],
                                 "signal_program_file": manifest["control"]["sha256"]}
    assert "candidate" not in runner.ledger_line(parent, None)
    tampered = {**manifest, "net": {**manifest["net"], "sha256": "1" * 64}}
    with pytest.raises(ValueError, match="does not hash"):
        runner.candidate_scenario(tampered, base=folder)
    with pytest.raises(ValueError, match="needs an id"):
        runner.candidate_scenario({**manifest, "id": ""}, base=folder)


def test_a_candidate_built_from_another_parent_is_refused(goal_file, prepared, tmp_path):
    runtime = runner.runtime_identity(ROOT)
    manifest, folder = _candidate_manifest(tmp_path, prepared)
    with pytest.raises(ValueError, match="names no parent"):
        runner.candidate_scenario({key: value for key, value in manifest.items() if key != "parent"}, base=folder)
    with pytest.raises(ValueError, match="names no parent"):
        runner.candidate_scenario({**manifest, "parent": {"net_sha256": "x"}}, base=folder)
    for key in ("net_sha256", "control_sha256"):
        other = {**manifest, "parent": {**manifest["parent"], key: "1" * 64}}
        with pytest.raises(ValueError, match="built from another parent"):
            runner.plan_judge(goal_file, tmp_path / "store", label="c", purpose="t", repository_root=ROOT,
                              runtime=runtime, candidate=runner.candidate_scenario(other, base=folder))
    assert not (tmp_path / "store").exists()  # refused before anything is staged


def test_a_candidate_judge_records_its_files(reader, goal_file, prepared, tmp_path):
    replay, calls = fake_runner(reader)
    manifest, folder = _candidate_manifest(tmp_path, prepared)
    plan = runner.plan_judge(goal_file, tmp_path / "store", label="F-test-k0", purpose="test", repository_root=ROOT,
                             candidate=runner.candidate_scenario(manifest, base=folder))
    result = runner.execute_plan(plan, main_rows=reader.read_csv("prepared/expected-counts.csv"),
                                 init_rows=reader.read_csv("prepared/initialization-counts.csv"), runner=replay,
                                 watch_memory=False)
    assert calls[0]["net_file"] == Path(plan.staged["net_file"]) and result["candidate"] == plan.request["candidate"]
    assert result["provenance"]["candidate"]["net"]["sha256"] == manifest["net"]["sha256"]
    assert runner.read_judge(plan)["request_sha256"] == plan.key


@pytest.mark.skipif(not (PILOT / "runs.jsonl").is_file(), reason="the M2 pilot store E:/twin-store/m2-pilot is absent")
def test_existing_m2_requests_and_cache_keys_are_unchanged(goal_file, tmp_path):
    """The nominal parent and a public-key draw re-plan to exactly the recorded request and key under their runtime."""
    rows = {json.loads(line)["id"]: json.loads(line) for line in (PILOT / "runs.jsonl").read_text(
        encoding="utf-8").splitlines() if line.strip()}
    for run_id, draw in (("P1-nominal-s104", None), ("P3-jitter-k1-s104", perturb.DrawKey.public_key("m2-pilot/k1"))):
        row = rows[run_id]
        recorded = json.loads((Path(row["run_dir"]) / "request.json").read_text(encoding="utf-8"))
        plan = runner.plan_judge(goal_file, tmp_path / "store", label=run_id, purpose="t", repository_root=ROOT,
                                 runtime=recorded["runtime"], draw=draw,
                                 family="depart_jitter" if draw is not None else None)
        assert plan.request == recorded and plan.key == row["request_sha256"], run_id


def test_the_judge_cli_plans_a_candidate_from_its_manifest(goal_file, prepared, tmp_path):
    import subprocess
    import sys

    manifest, folder = _candidate_manifest(tmp_path, prepared)
    path = folder / "candidate.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    command = [sys.executable, "-B", str(ROOT / "plugins/astra-sumo/scripts/astra_twin.py"), "judge", "--goal",
               str(goal_file), "--store", str(tmp_path / "store"), "--label", "F-test/k1", "--family", "depart_jitter",
               "--public-key", "fragility/F-test/k1", "--plan-only", "--json"]
    plain = subprocess.run(command, capture_output=True, text=True, timeout=600)
    done = subprocess.run([*command, "--candidate", str(path)], capture_output=True, text=True, timeout=600)
    assert plain.returncode == done.returncode == 0, done.stdout + done.stderr
    keys = [json.loads(run.stdout)["plans"][0]["request_sha256"] for run in (plain, done)]
    assert keys[0] != keys[1]
    assert not (tmp_path / "store" / "runs").exists()  # plan-only stages inputs and starts no run
