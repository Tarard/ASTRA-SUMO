"""Check six complete native trials and the separate failed seed preflight."""

import csv
import gzip
import hashlib
import io
import json
from pathlib import Path
import xml.etree.ElementTree as ET

import pytest

from astra_sumo.core.hamburg_readme_results import STATIONS

CASE = Path(__file__).resolve().parents[1] / "benchmarks/hamburg_ring1_directed_backlog_20261023_v1"
SOURCE = CASE.parent / "hamburg_ring1_119_straight_order_20261023_v1/full-native"
VARIANTS = ("merge", "exit-stage", "retiming", "tail", "tail27", "original-seed-restored")


def raw(path):
    return gzip.decompress(path.read_bytes())


def test_backlog_evidence_files_match_manifest():
    manifest = json.loads((CASE / "manifest.json").read_bytes())
    assert not manifest["formal_result"]
    assert {r["path"] for r in manifest["files"]} == {
        p.relative_to(CASE).as_posix()
        for p in CASE.rglob("*")
        if p.is_file() and p.name != "manifest.json" and "__pycache__" not in p.parts
    }
    for row in manifest["files"]:
        content = (CASE / row["path"]).read_bytes()
        assert len(content) == row["bytes"] and hashlib.sha256(content).hexdigest() == row["sha256"]


@pytest.mark.parametrize("variant", VARIANTS)
def test_all_130_and_40_backlog_values_match_native_records(variant):
    case = CASE / (variant + "-evaluation")
    evaluation = json.loads((case / "evaluation.json").read_bytes())
    run = json.loads(raw(case / "run.json.gz"))
    assert run["native_status"] == "pass" and run["observer_complete"] and run["missing_field_bins"] == 0
    measured = {
        (x.get("id"), float(x.get("begin")), float(x.get("end"))): int(x.get("nVehContrib"))
        for x in ET.fromstring(raw(case / "field-counts.xml.gz"))
    }
    fields = list(csv.DictReader((case / "field-comparison.csv").open(encoding="utf-8", newline="")))
    assert len(fields) == len({(x["detector_id"], x["begin"]) for x in fields}) == 130
    metadata = {
        (x["detector_id"], float(x["begin"]), float(x["end"])): x
        for x in csv.DictReader(io.StringIO(raw(case / "expected-counts.csv.gz").decode()))
    }
    residuals, groups = [], {}
    for row in fields:
        field, begin, end = row["detector_id"], float(row["begin"]), float(row["end"])
        count = measured[field, begin, end]
        assert count == int(row["measured_nVehContrib"])
        residual = count - int(row["expected_total"])
        assert residual == int(row["diff_nVehContrib_minus_expected"])
        residuals.append(residual)
        source = metadata[field, begin, end]
        for station, (node, assets) in STATIONS.items():
            if source["node_id"] == node and source["asset_id"] in assets:
                group = groups.setdefault((station, begin, end), [0, 0, 0, 0])
                group[0] += int(row["expected_total"])
                group[1] += count
                group[2] += residual
                group[3] += 1
    assert sum(map(abs, residuals)) / 130 == evaluation["MAE"]
    assert max(map(abs, residuals)) == evaluation["max_abs_error"]
    assert sum(x * x for x in residuals) == evaluation["loss"]
    stations = list(csv.DictReader((case / "station-comparison.csv").open(encoding="utf-8", newline="")))
    assert len(stations) == len(groups) == 40
    for row in stations:
        key = row["station_stream_id"], float(row["begin"]), float(row["end"])
        assert groups[key][:3] == [int(row[k]) for k in ("expected_total", "measured_nVehContrib", "residual")]
        assert groups[key][3] == len(STATIONS[key[0]][1]) and row["independent_evidence"] == "False"
    final = ET.fromstring(raw(case / "summary.xml.gz"))[-1]
    assert evaluation["completion"] == {k: int(final.get(k)) for k in evaluation["completion"]}
    assert not any(evaluation["completion"][k] for k in ("collisions", "teleports", "discarded"))
    assert evaluation["completion"]["loaded"] == (8640 if variant == "original-seed-restored" else 8603)
    names = (
        ("source-e1.add.xml", "expected-counts.csv")
        if variant in ("retiming", "original-seed-restored")
        else ("demand.rou.xml", "lane-intentions.json", "source-e1.add.xml", "expected-counts.csv")
    )
    for name in names:
        assert raw(case / (name + ".gz")) == raw(SOURCE / (name + ".gz")), name


@pytest.mark.parametrize("variant", VARIANTS)
def test_all182_targets_and_input_hashes_match_each_complete_native_trial(variant):
    case = CASE / (variant + "-evaluation")
    expected = list(csv.DictReader(io.StringIO(raw(CASE / "initialization-counts.csv.gz").decode())))
    expected += list(csv.DictReader(io.StringIO(raw(case / "expected-counts.csv.gz").decode())))
    targets = {(x["detector_id"], float(x["begin"]), float(x["end"])): int(x["expected_total"]) for x in expected}
    counts = {
        (x.get("id"), float(x.get("begin")), float(x.get("end"))): int(x.get("nVehContrib"))
        for x in ET.fromstring(raw(case / "field-counts.xml.gz"))
    }
    rows = list(csv.DictReader((case / "all182-comparison.csv").open(encoding="utf-8", newline="")))
    assert len(rows) == len(targets) == 182 and sum(float(x["begin"]) < 3600 for x in rows) == 52
    keys = [(x["detector_id"], float(x["begin"]), float(x["end"])) for x in rows]
    assert len(set(keys)) == 182 and set(keys) == targets.keys()
    for row, key in zip(rows, keys):
        assert int(row["expected_total"]) == targets[key]
        assert int(row["measured_nVehContrib"]) == counts[key]
        assert int(row["residual"]) == counts[key] - targets[key]
    summary = json.loads((CASE / "results.json").read_bytes())
    error = sum(abs(int(row["residual"])) for row in rows)
    assert summary["all182_TAE"][variant] == error
    verdict = json.loads((CASE / (variant + "-verdict.json")).read_bytes())
    assert summary["evaluations"][variant] == verdict and verdict["status"] == "rejected"
    if "after_all182_TAE" in verdict:
        assert verdict["after_all182_TAE"] == error
    run = json.loads(raw(case / "run.json.gz"))
    inputs = json.loads((case / "input-hashes.json").read_bytes())
    assert [{k: row[k] for k in ("path", "sha256")} for row in inputs["inputs"]] == run["inputs"]
    for entry in inputs["inputs"]:
        assert hashlib.sha256(raw(case / entry["snapshot"])).hexdigest() == entry["sha256"]
    initialization = inputs["initialization_targets"]
    assert hashlib.sha256(raw(case / initialization["snapshot"])).hexdigest() == initialization["sha256"]
    environment = json.loads((CASE / "environment.json").read_bytes())
    assert hashlib.sha256(raw(case / "network.net.xml.gz")).hexdigest() == environment["network_sha256"]


def test_restored_seed_uses_original_templates_and_matches_every_real_request_bin(tmp_path):
    from collections import Counter
    from astra_sumo.core.hamburg_od_spsa import write_hamburg_od_demand

    model_file = tmp_path / "od-model.json"
    model_file.write_bytes(raw(CASE / "seed-od-model.json.gz"))
    model = json.loads(model_file.read_bytes())
    values = [row["initial_flow"] for row in model["cells"]]
    assert sum(values) == 8640 and values == [len(row["templates"]) for row in model["cells"]]
    generated = write_hamburg_od_demand(model_file=model_file, values=values, output_dir=tmp_path / "generated")
    frozen = CASE / "original-seed-restored-evaluation"
    for name in ("demand.rou.xml", "lane-intentions.json"):
        assert (generated / name).read_bytes() == raw(frozen / (name + ".gz"))
    assert json.loads((frozen / "od-vector.json").read_bytes())["values"] == values
    intentions = json.loads((generated / "lane-intentions.json").read_bytes())["vehicles"]
    requests = Counter((row["field_id"], model["cells"][row["od_cell"]]["begin_s"]) for row in intentions)
    targets = list(csv.DictReader((frozen / "all182-comparison.csv").open(encoding="utf-8", newline="")))
    assert requests == Counter(
        {(row["detector_id"], float(row["begin"])): int(row["expected_total"]) for row in targets}
    )


def test_wrong_8622_seed_preflight_is_not_a_native_result_or_a_zero_measurement():
    failure = json.loads((CASE / "original-seed-preflight-failure.json").read_bytes())
    summary = json.loads((CASE / "results.json").read_bytes())
    assert set(summary["evaluations"]) == set(VARIANTS) and summary["completed_native_evaluations"] == 6
    assert not summary["stage_goal_met"] and summary["selected_stage_candidate"] is None
    assert summary["preflight_failures"] == {"original-seed": failure}
    assert failure["status"] == "preflight_failed" and not failure["native_run_started"]
    assert failure["native_evaluation"] is failure["completion"] is failure["field_metrics"] is None
    assert sum(json.loads(raw(CASE / "original-seed-initial-od-vector.json.gz"))["values"]) == 8622
    assert failure["requested_vehicle_count"] == 8622 and failure["required_original_seed_vehicle_count"] == 8640
    assert b"AssertionError" in raw(CASE / "original-seed.stderr.log.gz")
    assert raw(CASE / "original-seed.stdout.log.gz") == b""
    assert not (CASE / "original-seed-evaluation").exists()
    for name, digest in failure["input_hashes"].items():
        assert hashlib.sha256(raw(CASE / name)).hexdigest() == digest


def test_control_changes_are_sequential_and_keep_official_programs():
    roots = [
        ET.fromstring(raw(path))
        for path in (
            SOURCE / "signal-programs.add.xml.gz",
            CASE / "merge-evaluation/signal-programs.add.xml.gz",
            CASE / "exit-stage-evaluation/signal-programs.add.xml.gz",
        )
    ]
    programs = [{x.get("id"): ET.tostring(x) for x in root} for root in roots]
    for before, after, identity in zip(programs, programs[1:], ("GS_266199063", "266197525")):
        assert before.keys() == after.keys()
        assert {key for key in before if before[key] != after[key]} == {identity}
    assert all(programs[0][key] == programs[2][key] for key in programs[0] if key.startswith("LSA"))
    assert json.loads((CASE / "merge-verdict.json").read_bytes())["status"] == "rejected"
    for variant in ("tail", "tail27"):
        control = ET.fromstring(raw(CASE / (variant + "-evaluation/signal-programs.add.xml.gz")))
        candidate = {row.get("id"): ET.tostring(row) for row in control}
        assert candidate.keys() == programs[1].keys()
        assert {key for key in candidate if candidate[key] != programs[1][key]} == {"GS_266199063"}
    assert raw(CASE / "original-seed-restored-evaluation/signal-programs.add.xml.gz") == raw(
        CASE / "merge-evaluation/signal-programs.add.xml.gz"
    )


def test_timing_changes_use_all182_real_values_and_preserve_explicit_routes():
    from astra_sumo.core.native_temporal_feedback import propose_native_count_retiming

    parent, trial = CASE / "merge-evaluation", CASE / "retiming-evaluation"
    source = {v.get("id"): v for v in ET.fromstring(raw(parent / "demand.rou.xml.gz")).findall("vehicle")}
    actual = {v.get("id"): v for v in ET.fromstring(raw(trial / "demand.rou.xml.gz")).findall("vehicle")}
    intents = json.loads(raw(parent / "lane-intentions.json.gz"))["vehicles"]
    assigned = {x["vehicle_id"]: x["field_id"] for x in intents}
    assert assigned == {
        x["vehicle_id"]: x["field_id"] for x in json.loads(raw(trial / "lane-intentions.json.gz"))["vehicles"]
    }
    counts = {
        (r.get("id"), float(r.get("begin")), float(r.get("end"))): int(r.get("nVehContrib"))
        for r in ET.fromstring(raw(parent / "field-counts.xml.gz"))
    }
    targets = list(csv.DictReader(io.StringIO(raw(CASE / "initialization-counts.csv.gz").decode())))
    targets += list(csv.DictReader(io.StringIO(raw(parent / "expected-counts.csv.gz").decode())))
    for row in targets:
        row["measured_nVehContrib"] = counts[row["detector_id"], float(row["begin"]), float(row["end"])]
    proposal = propose_native_count_retiming(
        requests=[
            {"vehicle_id": i, "detector_id": assigned[i], "requested_depart_s": float(v.get("depart"))}
            for i, v in source.items()
        ],
        passages=json.loads(raw(parent / "native-passages.json.gz"))["passages"],
        measurements=targets,
        max_moves_per_field=64,
        max_shift_s=2700,
    )
    frozen = json.loads((CASE / "retiming-proposal.json").read_bytes())
    assert proposal["proposals"] == frozen["proposals"] and len(targets) == 182
    changed = {x["vehicle_id"]: x for x in proposal["proposals"]}
    assert len(changed) == 345 and source.keys() == actual.keys() and len(actual) == 8603
    for identity, vehicle in actual.items():
        expected = (
            changed[identity]["proposed_depart_s"] if identity in changed else float(source[identity].get("depart"))
        )
        assert float(vehicle.get("depart")) == pytest.approx(expected, abs=1e-8)
        vehicle.set("depart", source[identity].get("depart"))
        assert ET.tostring(vehicle) == ET.tostring(source[identity])
    rows = list(csv.DictReader((trial / "all182-comparison.csv").open(encoding="utf-8", newline="")))
    assert len(rows) == 182 and sum(float(x["begin"]) < 3600 for x in rows) == 52
    assert raw(trial / "signal-programs.add.xml.gz") == raw(parent / "signal-programs.add.xml.gz")
