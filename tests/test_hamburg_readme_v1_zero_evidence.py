"""Check every frozen historical V1 trial without running SUMO."""

import csv
from collections import Counter
import gzip
import hashlib
import io
import json
import math
from pathlib import Path
import xml.etree.ElementTree as ET


CASE = Path(__file__).resolve().parents[1] / "benchmarks/hamburg_ring1_readme_v1_zero_20261023_v1"


def _raw(path):
    raw = path.read_bytes()
    return gzip.decompress(raw) if path.suffix == ".gz" else raw


def test_v1_evidence_hashes_scope_and_bounded_history():
    manifest = json.loads((CASE / "manifest.json").read_bytes())
    assert not manifest["formal_result"] and manifest["exact_count_fit"]
    listed = {row["path"] for row in manifest["files"]}
    assert listed == {p.relative_to(CASE).as_posix() for p in CASE.rglob("*")
                      if p.is_file() and p.name != "manifest.json" and "__pycache__" not in p.parts}
    for row in manifest["files"]:
        raw = (CASE / row["path"]).read_bytes()
        assert len(raw) == row["bytes"] and hashlib.sha256(raw).hexdigest() == row["sha256"]
    verdict = json.loads((CASE / "verdict.json").read_bytes())
    assert verdict["status"] == "exact_historical_station_fit"
    assert verdict["best"]["mae"] == 0 and verdict["best"]["exact_bins"] == 40
    assert verdict["greedy_best"]["mae"] == 0.05
    assert verdict["reproduction"]["mae"] == 0.15
    assert verdict["historical_count_definition"] != verdict["native_comparison_count_definition"]
    assert len(verdict["limitations"]) == 3 and not verdict["october_130_field_acceptance"]
    greedy = verdict["searches"]["ten-trial"]["trials"]
    assert len(greedy) == 10 and sum(t["accepted"] for t in greedy) == 1
    assert [t["total_absolute_error"] for t in greedy] == [2, 4, 4, 2, 6, 6, 4, 6, 6, 4]
    branch = verdict["searches"]["branch"]["trials"]
    assert [t["total_absolute_error"] for t in branch] == [6, 4, 2, 0]
    assert len(verdict["runs"]) == 22


def test_all_v1_counts_and_health_match_native_records():
    bank = json.loads((CASE / "inputs/station-detector-bank.json").read_bytes())
    stations = {d: s["station_stream_id"] for s in bank for d in s["detector_ids"]}
    counts = list(csv.DictReader(io.StringIO((CASE / "inputs/counts.simulation.15min.csv").read_text(encoding="utf-8"))))
    official = {(int(r["stream_id"]), int(r["begin"])): int(r["expected_total"]) for r in counts}
    targets = {(s["station_stream_id"], begin):
               sum(official[int(stream), begin] for d in s["detector_sources"] for stream in d["source_stream_ids"])
               for s in bank for begin in range(0, 9000, 900)}
    assert len(stations) == 12 and len(targets) == 40
    verdict = json.loads((CASE / "verdict.json").read_bytes())
    for record in verdict["runs"]:
        run = CASE / "native" / record["name"]
        audit = json.loads((run / "count-audit.json").read_bytes())
        demand = ET.fromstring(_raw(run / "demand.rou.xml.gz"))
        vehicles = {v.get("id") for v in demand.findall("vehicle")}
        assert len(vehicles) == len(demand.findall("vehicle")) == 5037
        unique = {key: set() for key in targets}
        entered = Counter()
        for event in ET.fromstring(_raw(run / "instant-e1.xml.gz")):
            if event.get("state") == "enter":
                assert event.get("vehID") in vehicles
                time = math.ceil(math.nextafter(float(event.get("time")), -math.inf))
                detector = event.get("id").removeprefix("instant_")
                key = (stations[detector], math.floor((time - 1800) / 900) * 900)
                if key in unique:
                    unique[key].add(event.get("vehID"))
                    entered[detector, key[1]] += 1
        native = {key: 0 for key in targets}
        covered = set()
        for interval in ET.fromstring(_raw(run / "physical-e1-15min.xml.gz")):
            key = (stations[interval.get("id")], int(float(interval.get("begin"))) - 1800)
            if key in native:
                native[key] += int(interval.get("nVehContrib"))
                detector_key = interval.get("id"), key[1]
                assert detector_key not in covered
                covered.add(detector_key)
                assert entered[detector_key] == int(interval.get("nVehEntered"))
        assert covered == {(d, begin) for d in stations for begin in range(0, 9000, 900)}
        assert len(audit["comparisons"]) == 40
        for row in audit["comparisons"]:
            key = row["station_stream_id"], row["begin"]
            assert row["target"] == targets[key]
            assert row["measured"] == len(unique[key])
            assert row["error"] == row["measured"] - row["target"]
        assert sum(abs(r["error"]) for r in audit["comparisons"]) / 40 == audit["mae"]
        native_report = json.loads((run / "native-contrib-comparison.json").read_bytes())
        assert len(native_report["comparisons"]) == 40
        for row in native_report["comparisons"]:
            key = row["station_stream_id"], row["begin"]
            assert row["target"] == targets[key] and row["measured"] == native[key]
            assert row["error"] == native[key] - targets[key]
        assert sum(abs(native[k] - targets[k]) for k in targets) / 40 == native_report["mae"]
        assert native_report["mae"] > audit["mae"]
        last = ET.fromstring(_raw(run / "summary.xml.gz"))[-1]
        assert audit["completion"] == {k: int(last.get(k)) for k in audit["completion"]}
        assert audit["healthy"] and audit["completion"] == dict(
            loaded=5037, inserted=5037, arrived=5037, running=0, waiting=0, teleports=0, collisions=0, discarded=0
        )
        assert len(ET.fromstring(_raw(run / "collisions.xml.gz"))) == 0
        trips = ET.fromstring(_raw(run / "tripinfo.xml.gz"))
        assert {v.get("id") for v in trips} == vehicles and len(trips) == 5037
        assert not (run / "sumo-error.log").read_bytes()
        command = json.loads((run / "command.json").read_bytes())
        assert command["status"] == "pass" and command["returncode"] == 0
        assert ".venv/Lib/site-packages/sumo/bin/sumo.exe" in command["command"][0].replace("\\", "/")
        assert command["command"][command["command"].index("--step-length") + 1] == "1"


def test_branch_source_and_final_demand_retain_observations_and_routes():
    adaptation = json.loads((CASE / "branch-source/source-adaptation.json").read_bytes())
    assert not adaptation["parent_accepted"] and adaptation["parent_trial"] == 2
    for row in adaptation["files"]:
        path = CASE / "branch-source" / row["path"]
        if path.suffix == ".xml":
            path = path.with_name(path.name + ".gz")
        assert hashlib.sha256(_raw(path)).hexdigest() == row["sha256"]
    for name in ("network.net.xml.gz", "e1.add.xml.gz", "instant.add.xml.gz",
                 "station-detector-bank.json", "counts.simulation.15min.csv"):
        assert _raw(CASE / "inputs" / name) == _raw(CASE / "branch-source" / name)
    assert _raw(CASE / "branch-source/demand.rou.xml.gz") == _raw(
        CASE / "native/ten-trial/trial-002/demand.rou.xml.gz"
    )
    adapted = json.loads((CASE / "branch-source/count-audit.json").read_bytes())
    rejected = json.loads((CASE / "native/ten-trial/trial-002/count-audit.json").read_bytes())
    assert adapted["comparisons"] == rejected["comparisons"]
    original = ET.fromstring(_raw(CASE / "inputs/demand.rou.xml.gz"))
    final = ET.fromstring(_raw(CASE / "native/branch/trial-004/demand.rou.xml.gz"))
    before = {v.get("id"): v for v in original.findall("vehicle")}
    after = {v.get("id"): v for v in final.findall("vehicle")}
    assert set(before) == set(after)
    changes = {}
    for vid in before:
        assert ET.tostring(before[vid].find("route")) == ET.tostring(after[vid].find("route"))
        delta = round(float(after[vid].get("depart")) - float(before[vid].get("depart")), 8)
        if delta:
            changes[vid] = delta
    assert changes == dict(sensor_schedule_930=3, sensor_schedule_1438=3,
                           sensor_schedule_1941=14, sensor_schedule_3465=-14)
    native = json.loads((CASE / "native/branch/trial-004/native-contrib-comparison.json").read_bytes())
    assert native["mae"] == 12.825 and native["total_absolute_error"] == 513
