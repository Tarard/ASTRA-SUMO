"""Check the rejected directed trials against their complete native records."""
import csv
import gzip
import hashlib
import json
from pathlib import Path
import xml.etree.ElementTree as ET

import pytest

CASE = Path(__file__).resolve().parents[1] / "benchmarks/hamburg_ring1_directed_od_time_20261023_v1"


def test_directed_evidence_manifest():
    manifest = json.loads((CASE / "manifest.json").read_bytes())
    assert not manifest["formal_result"]
    listed = {row["path"] for row in manifest["files"]}
    assert listed == {p.relative_to(CASE).as_posix() for p in CASE.rglob("*") if p.is_file() and p.name != "manifest.json"}
    for row in manifest["files"]:
        raw = (CASE / row["path"]).read_bytes()
        assert len(raw) == row["bytes"] and hashlib.sha256(raw).hexdigest() == row["sha256"]


@pytest.mark.parametrize("name", ["first-trial", "unit-step"])
def test_rejected_directed_trials_keep_all_native_counts(name):
    trial = CASE / name
    evaluation = json.loads((trial / "evaluation.json").read_bytes())
    verdict = json.loads((trial / "verdict.json").read_bytes())
    assert verdict["status"] == "rejected" and not verdict["formal_result"]
    assert evaluation["MAE"] > verdict["before"]["MAE"] and not evaluation["healthy"]
    native = ET.fromstring(gzip.decompress((trial / "field-counts.xml.gz").read_bytes()))
    measured = {(r.get("id"), float(r.get("begin")), float(r.get("end"))): int(r.get("nVehContrib")) for r in native}
    with (trial / "field-comparison.csv").open(encoding="utf-8", newline="") as stream:
        fields = list(csv.DictReader(stream))
    assert len(fields) == 130 and len({(r["detector_id"], r["begin"]) for r in fields}) == 130
    residuals = []
    for row in fields:
        count = measured[row["detector_id"], float(row["begin"]), float(row["end"])]
        assert count == int(row["measured_nVehContrib"])
        residual = count - int(row["expected_total"])
        assert residual == int(row["diff_nVehContrib_minus_expected"])
        residuals.append(residual)
    assert sum(map(abs, residuals)) / 130 == evaluation["MAE"]
    assert sum(r * r for r in residuals) == evaluation["loss"]
    assert max(map(abs, residuals)) == evaluation["max_abs_error"]
    with (trial / "station-comparison.csv").open(encoding="utf-8", newline="") as stream:
        stations = list(csv.DictReader(stream))
    assert len(stations) == 40 and all(r["independent_evidence"] == "False" for r in stations)
    assert sum(abs(int(r["residual"])) for r in stations) / 40 == evaluation["station_MAE"]
    summary = ET.fromstring(gzip.decompress((trial / "summary.xml.gz").read_bytes()))[-1]
    assert evaluation["completion"] == {key: int(summary.get(key)) for key in evaluation["completion"]}
    assert not any(evaluation["completion"][key] for key in ("collisions", "teleports", "discarded"))
    assert evaluation["completion"]["loaded"] == sum(json.loads((trial / "od-vector.json").read_bytes())["integer_counts"]) == 8603
