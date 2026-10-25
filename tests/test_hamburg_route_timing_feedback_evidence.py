"""Offline checks of seven full route/timing trials and their immutable evidence."""

from collections import Counter, defaultdict
import csv
import gzip
import hashlib
import io
import json
import math
from pathlib import Path
import xml.etree.ElementTree as ET

import pytest

from astra_sumo.core.hamburg_readme_results import STATIONS

CASE = Path(__file__).resolve().parents[1] / "benchmarks/hamburg_ring1_route_timing_feedback_20261023_v1"
PRIMARY = CASE.parent / "hamburg_ring1_primary_branch_repair_20261023_v1"
RUNS = (
    "z15-residential-full",
    "z15-half-full",
    "z15-half-unsplit-full",
    "timing-01-16",
    "timing-01-32",
    "timing-01-64",
    "timing-01-128",
)


def raw(path):
    return gzip.decompress(path.read_bytes())


def read_json(path):
    return json.loads(raw(path))


def rows(path):
    return list(csv.DictReader(io.StringIO(raw(path).decode("utf-8"))))


def native_counts(case):
    return {
        (r.get("id"), float(r.get("begin")), float(r.get("end"))): int(r.get("nVehContrib"))
        for r in ET.fromstring(raw(case / "native/field-counts.xml.gz"))
    }


def test_manifest_and_all_compressed_snapshots_preserve_declared_bytes():
    manifest = json.loads((CASE / "manifest.json").read_bytes())
    actual = {
        p.relative_to(CASE).as_posix()
        for p in CASE.rglob("*")
        if p.is_file() and p.name != "manifest.json" and "__pycache__" not in p.parts
    }
    assert {row["path"] for row in manifest["files"]} == actual
    for row in manifest["files"]:
        value = (CASE / row["path"]).read_bytes()
        assert len(value) == row["bytes"] and hashlib.sha256(value).hexdigest() == row["sha256"]
    for row in json.loads((CASE / "source-provenance.json").read_bytes())["snapshots"]:
        value = raw(CASE / row["snapshot"])
        assert len(value) == row["raw_bytes"] and hashlib.sha256(value).hexdigest() == row["raw_sha256"]
    local = json.loads((CASE / "local-only-large-traces.json").read_bytes())["files"]
    assert len(local) == 14 and {row["run"] for row in local} == set(RUNS)
    assert not list(CASE.rglob("instant-e1.xml.gz")) and not list(CASE.rglob("queues.xml.gz"))
    trace = read_json(CASE / "timing-source-passages.json.gz")
    assert trace["reconciled"]
    contributions = Counter(
        (row["detector_id"], row["interval_begin_s"], row["interval_end_s"]) for row in trace["passages"]
    )
    assert contributions == Counter(native_counts(CASE / "z15-half-unsplit-full"))
    assert len({row["vehicle_id"] for row in trace["passages"]}) == len(trace["passages"])
    instant = next(
        row
        for row in local
        if row["run"] == "z15-half-unsplit-full" and row["path"].replace("\\", "/").endswith("/instant-e1.xml")
    )
    assert trace["inputs"][0]["sha256"] == instant["sha256"]


@pytest.mark.parametrize("name", RUNS)
def test_all130_40_and182_results_match_native_counts_and_completion(name):
    case = CASE / name
    expected = rows(case / "prepared/expected-counts.csv.gz")
    initialization = rows(case / "prepared/initialization-counts.csv.gz")
    counts = native_counts(case)
    fields = rows(case / "native/field-comparison.csv.gz")
    stations = rows(case / "evaluation/station-comparison.csv.gz")
    full = rows(case / "evaluation/all182-comparison.csv.gz")
    assert len(counts) == 208 and len(fields) == len(expected) == 130
    assert len(initialization) == 52 and len(full) == 182 and len(stations) == 40
    source = {(r["detector_id"], float(r["begin"]), float(r["end"])): r for r in expected + initialization}
    assert len(source) == 182
    groups, residuals = defaultdict(lambda: [0, 0, 0, 0]), []
    for row in fields:
        key = row["detector_id"], float(row["begin"]), float(row["end"])
        target = source[key]
        assert int(row["expected_total"]) == int(target["expected_total"])
        diff = counts[key] - int(target["expected_total"])
        assert int(row["measured_nVehContrib"]) == counts[key]
        assert int(row["diff_nVehContrib_minus_expected"]) == diff
        residuals.append(diff)
        for station, (node, assets) in STATIONS.items():
            if target["node_id"] == node and target["asset_id"] in assets:
                group = groups[station, key[1], key[2]]
                group[0] += int(target["expected_total"])
                group[1] += counts[key]
                group[2] += diff
                group[3] += 1
    assert len(groups) == 40
    for row in stations:
        key = row["station_stream_id"], float(row["begin"]), float(row["end"])
        assert groups[key][:3] == [int(row[k]) for k in ("expected_total", "measured_nVehContrib", "residual")]
        assert groups[key][3] == len(STATIONS[key[0]][1]) and row["independent_evidence"] == "False"
    seen = set()
    for row in full:
        key = row["detector_id"], float(row["begin"]), float(row["end"])
        assert key not in seen
        seen.add(key)
        assert int(row["expected_total"]) == int(source[key]["expected_total"])
        assert int(row["measured_nVehContrib"]) == counts[key]
        assert int(row["residual"]) == counts[key] - int(source[key]["expected_total"])
    assert seen == source.keys()
    evaluation = read_json(case / "evaluation/evaluation.json.gz")
    verdict = read_json(case / "verdict.json.gz")
    assert evaluation == verdict["after"]
    assert evaluation["MAE"] == sum(map(abs, residuals)) / 130
    assert evaluation["loss"] == sum(value * value for value in residuals)
    assert evaluation["max_abs_error"] == max(map(abs, residuals))
    assert evaluation["station_MAE"] == sum(abs(value[2]) for value in groups.values()) / 40
    assert verdict["all182_TAE"] == sum(abs(int(row["residual"])) for row in full)
    final = None
    for _, row in ET.iterparse(io.BytesIO(raw(case / "native/summary.xml.gz"))):
        if row.tag == "step":
            final = dict(row.attrib)
        row.clear()
    assert evaluation["completion"] == {key: int(final[key]) for key in evaluation["completion"]}
    assert evaluation["completion"]["loaded"] == 8640
    collisions = ET.fromstring(raw(case / "native/collisions.xml.gz")).findall("collision")
    assert len(collisions) == evaluation["completion"]["collisions"] == (2 if name == "z15-half-full" else 0)
    if name == "z15-half-full":
        assert evaluation["completion"]["teleports"] == 2 and verdict["status"] == "rejected"
    else:
        assert not any(evaluation["completion"][key] for key in ("collisions", "teleports", "discarded"))
    assert not verdict["formal_result"] and not verdict["zero_residual_goal_complete"]


@pytest.mark.parametrize("name", RUNS)
def test_actual_inputs_execution_hashes_and_observations_remain_bound(name):
    case = CASE / name
    metadata = json.loads((case / "input-hashes.json").read_bytes())
    run = read_json(case / "native/run.json.gz")
    assert run["native_status"] == "pass" and run["observer_complete"] and run["missing_field_bins"] == 0
    assert [{k: row[k] for k in ("path", "sha256")} for row in metadata["inputs"]] == run["inputs"]
    for row in metadata["inputs"]:
        assert hashlib.sha256(raw(case / row["snapshot"])).hexdigest() == row["sha256"]
    initialization = metadata["initialization_targets"]
    assert hashlib.sha256(raw(case / initialization["snapshot"])).hexdigest() == initialization["sha256"]
    preflight = read_json(case / "preflight.json.gz")
    for file, expected in preflight["input_sha256"].items():
        assert hashlib.sha256(raw(case / "prepared" / (file + ".gz"))).hexdigest() == expected
    for file, expected in preflight["implementation_sha256"].items():
        assert hashlib.sha256(raw(CASE / "code-source" / (file + ".gz"))).hexdigest() == expected
    for file in ("expected-counts.csv", "initialization-counts.csv", "field-loops.add.xml", "control.add.xml"):
        assert raw(case / "prepared" / (file + ".gz")) == raw(PRIMARY / "prepared" / (file + ".gz"))
    monitor = read_json(case / "native/stall-monitor.json.gz")
    assert monitor["status"] == "completed" and monitor["simulation_time_s"] == 14400


def test_only_declared_Z15_tails_change_and_unsafe_raw_verdict_stays_rejected():
    source = {v.get("id"): v for v in ET.fromstring(raw(PRIMARY / "prepared/demand.rou.xml.gz")).findall("vehicle")}
    fields = {
        r["vehicle_id"]: r["field_id"] for r in read_json(PRIMARY / "prepared/lane-intentions.json.gz")["vehicles"]
    }
    for name, count in ((RUNS[0], 1205), (RUNS[1], 603), (RUNS[2], 603)):
        actual = {
            v.get("id"): v for v in ET.fromstring(raw(CASE / name / "prepared/demand.rou.xml.gz")).findall("vehicle")
        }
        assert source.keys() == actual.keys() and len(actual) == 8640
        changed = []
        for identity, old in source.items():
            new = actual[identity]
            if new.find("route").get("edges") != old.find("route").get("edges"):
                changed.append(identity)
                assert fields[identity] == "hh_0119_Z_15_29375"
                assert new.find("route").get("edges") == old.find("route").get("edges").replace(
                    "1108564798#0 1108564798#1", "27064186#0 1073054122#1 1345098540"
                )
            new.find("route").set("edges", old.find("route").get("edges"))
            assert ET.tostring(old) == ET.tostring(new)
        assert len(changed) == count
    assert raw(CASE / RUNS[1] / "prepared/demand.rou.xml.gz") == raw(CASE / RUNS[2] / "prepared/demand.rou.xml.gz")
    results = json.loads((CASE / "results.json").read_bytes())
    assert results["complete_runs"] == list(RUNS) and not results["zero_residual_goal_complete"]
    assert results["best_safe_diagnostic"] == RUNS[2]
    assert all(results["evaluations"][name] == read_json(CASE / name / "verdict.json.gz") for name in RUNS)


@pytest.mark.parametrize("label,count", [(16, 16), (32, 32), (64, 64), (128, 107)])
def test_failed_timing_batches_retain_explicit_requests_and_valid_unverified_projection(label, count):
    source = CASE / "z15-half-unsplit-full"
    case = CASE / ("timing-01-" + str(label))
    proposal = read_json(case / "proposal.json.gz")
    before = {v.get("id"): v for v in ET.fromstring(raw(source / "prepared/demand.rou.xml.gz")).findall("vehicle")}
    after = {v.get("id"): v for v in ET.fromstring(raw(case / "prepared/demand.rou.xml.gz")).findall("vehicle")}
    intent = {r["vehicle_id"]: r for r in read_json(source / "prepared/lane-intentions.json.gz")["vehicles"]}
    actual_intent = {r["vehicle_id"]: r for r in read_json(case / "prepared/lane-intentions.json.gz")["vehicles"]}
    assert before.keys() == after.keys() == actual_intent.keys() and len(after) == 8640
    moves = {r["vehicle_id"]: r for r in proposal["proposals"]}
    assert len(moves) == proposal["changed_vehicles"] == count
    pairs = sorted(
        {(v.find("route").get("edges").split()[0], v.find("route").get("edges").split()[-1]) for v in before.values()}
    )
    pair_index = {pair: index for index, pair in enumerate(pairs)}
    assert len(pairs) == 21
    actual_cells = [0] * 294
    for identity, vehicle in after.items():
        original = before[identity]
        expected = moves[identity]["proposed_depart_s"] if identity in moves else float(original.get("depart"))
        assert float(vehicle.get("depart")) == pytest.approx(expected, abs=1e-8)
        candidate_depart = vehicle.get("depart")
        edges = vehicle.find("route").get("edges").split()
        cell = int(float(candidate_depart) // 900) * 21 + pair_index[edges[0], edges[-1]]
        actual_cells[cell] += 1
        assert actual_intent[identity]["od_cell"] == cell
        assert actual_intent[identity]["depart_s"] == float(candidate_depart)
        vehicle.set("depart", original.get("depart"))
        assert ET.tostring(vehicle) == ET.tostring(original)
        vehicle.set("depart", candidate_depart)
        assert actual_intent[identity]["field_id"] == intent[identity]["field_id"]
    vector = read_json(case / "evaluation/od-vector.json.gz")
    assert vector["values"] == vector["integer_counts"] == actual_cells and vector["vehicle_count"] == 8640
    assert "no quantile regeneration" in vector["generation"]
    counts = native_counts(source)
    targets = {
        (r["detector_id"], float(r["begin"]), float(r["end"])): int(r["expected_total"])
        for r in rows(source / "prepared/expected-counts.csv.gz")
        + rows(source / "prepared/initialization-counts.csv.gz")
    }
    main = {key for key in targets if 3600 <= key[1] and key[2] <= 12600}
    events = {r["vehicle_id"]: r for r in read_json(CASE / "timing-source-passages.json.gz")["passages"]}
    projected, field_moves, source_moves = counts.copy(), Counter(), Counter()

    def score(values, keys):
        return sum(abs(values[key] - targets[key]) for key in keys)

    previous = score(projected, main)
    assert previous == proposal["before_objective_l1"] == 775
    for move in proposal["proposals"]:
        old, new = tuple(move["source_interval"]), tuple(move["target_interval"])
        event = events[move["vehicle_id"]]
        assert old == (event["detector_id"], event["interval_begin_s"], event["interval_end_s"])
        shift_ms = math.floor(move["proposed_depart_s"] * 1000 + 0.5) - math.floor(move["source_depart_s"] * 1000 + 0.5)
        notification = -(-(round(event["notification_time_s"] * 1000) + shift_ms) // 100) * 0.1
        assert new[1] <= notification < new[2]
        assert notification == pytest.approx(move["projected_notification_time_s"])
        projected[old] -= 1
        projected[new] += 1
        assert min(projected.values()) >= 0
        current = score(projected, main)
        assert current < previous
        previous = current
        field_moves[move["detector_id"]] += 1
        source_moves[old] += 1
    assert max(field_moves.values()) <= 24 and max(source_moves.values()) <= 8
    assert sum(projected.values()) == sum(counts.values())
    assert previous == proposal["projected_objective_l1"]
    assert score(projected, targets) == proposal["projected_all_l1"]
    verdict = read_json(case / "verdict.json.gz")
    assert verdict["status"] == "rejected" and not proposal["native_response_verified"]
    actual = sum(abs(int(r["diff_nVehContrib_minus_expected"])) for r in rows(case / "native/field-comparison.csv.gz"))
    assert actual > proposal["before_objective_l1"] > proposal["projected_objective_l1"]


def test_three_short_prefixes_keep_unknown_main_window_and_reference_full_native_counts():
    primary = {
        (r.get("id"), r.get("begin"), r.get("end")): r.get("nVehContrib")
        for r in ET.fromstring(raw(PRIMARY / "evaluation/native/field-counts.xml.gz"))
    }
    for name in ("baseline-prefix-900", "z15-residential-prefix-900", "z15-half-prefix-900"):
        case = CASE / "prefixes" / name
        result = read_json(case / "result.json.gz")
        fields = ET.fromstring(raw(case / "field-counts.xml.gz"))
        assert result["end_s"] == 900 and result["main_window_counts"] is None and len(fields) == 13
        if name == "baseline-prefix-900":
            assert result["first_13_counts_identical"]
            assert all(primary[r.get("id"), r.get("begin"), r.get("end")] == r.get("nVehContrib") for r in fields)
    assert (CASE.parent / "hamburg_ring1_119_single_split_20261023_v1/manifest.json").is_file()
