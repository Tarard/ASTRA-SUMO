"""Freeze the completed first SPSA round. Do not start or modify simulations."""

from pathlib import Path
import gzip
import hashlib
import json
import shutil
import xml.etree.ElementTree as ET

import numpy as np

from astra_sumo.core.detector_demand import audit_expected_to_e1_strict, read_csv_rows


root = Path.cwd()
source = root / "outputs/hamburg-od-spsa-standard-20261022-v2"
out = Path(__file__).resolve().parent
assert not (out / "result.json").exists(), "Preserve this frozen version. Use a new version."
result = json.loads((source / "result.json").read_bytes())
settings = json.loads((source / "settings.json").read_bytes())
assert result["completed_iterations"] == settings["iterations"] == 1
assert not result["formal_result"] and result["final_vector_remeasured"]
row = json.loads((source / "iteration-0001.json").read_bytes())
theta, delta = np.array(row["theta"]), np.array(row["delta"])
assert np.array_equal(np.array(row["next_theta"]), np.maximum(0, theta - row["ak"] * np.array(row["gradient"])))
roundoff = float(np.max(np.abs(np.array(row["next_theta"]) - np.maximum(0, theta + delta))))
assert roundoff <= 1e-12
assert np.array_equal(np.floor(np.array(row["next_theta"]) + .5), np.maximum(0, theta + delta))
assert np.array_equal(np.array(result["theta"]), np.array(row["next_theta"]))
assert row["a"] == row["ak"] == 1 / 3798
assert (row["loss_plus"], row["loss_minus"], row["ck"]) == (63702, 78894, 2)
for name in ("result.json", "settings.json", "evaluations.json", "iteration-0001.json", "initial-reuse.json", "README.md"):
    target = "run-report.md" if name == "README.md" else name
    shutil.copyfile(source / name, out / target)
shutil.copyfile(source.with_name(source.name + "-execution") / "execution.json", out / "execution.json")
shutil.copyfile(source.with_name(source.name + "-execution") / "run.py", out / "run-at-execution.py")
inventory, local_only, safety = [], [], []
for evaluation in result["evaluations"]:
    name = evaluation["name"]
    case = root / "outputs/hamburg-od-initial-native-20261022-v1" if name == "initial" else source / name
    native = case / "native"
    destination = out / "evaluations" / name
    destination.mkdir(parents=True)
    expected = read_csv_rows(native / "expected-counts.csv")
    rows = audit_expected_to_e1_strict(expected, native / "field-counts.xml", count_attribute="nVehContrib")
    assert len(rows) == 130 and all(r["measured_nVehContrib"] is not None for r in rows)
    errors = [r["diff_nVehContrib_minus_expected"] for r in rows]
    assert sum(r * r for r in errors) == evaluation["loss"]
    assert sum(map(abs, errors)) / 130 == evaluation["MAE"]
    assert max(map(abs, errors)) == evaluation["max_abs_error"]
    station_file = (root / "outputs/hamburg-od-initial-analysis-20261022-v2/all-40-station-comparison.csv"
                    if name == "initial" else case / "station-comparison.csv")
    stations = read_csv_rows(station_file)
    assert len(stations) == 40
    assert sum(abs(int(r["residual"])) for r in stations) / 40 == evaluation["station_MAE"]
    shutil.copyfile(native / "field-comparison.csv", destination / "field-comparison.csv")
    shutil.copyfile(station_file, destination / "station-comparison.csv")
    shutil.copyfile(case / "od-vector.json", destination / "od-vector.json")
    counts = json.loads((case / "od-vector.json").read_bytes())
    assert counts["vehicle_count"] == evaluation["completion"]["loaded"]
    for path in sorted(native.iterdir()):
        if not path.is_file():
            continue
        record = {"evaluation": name, "source": str(path), "bytes": path.stat().st_size,
                  "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
        inventory.append(record)
        if path.name in {"instant-e1.xml", "queues.xml"}:
            local_only.append(record)
            continue
        target = destination / (path.name + ".gz")
        target.write_bytes(gzip.compress(path.read_bytes(), compresslevel=3, mtime=0))
    collisions = [dict(r.attrib) for r in ET.parse(native / "collisions.xml").getroot().findall("collision")]
    assert len(collisions) == evaluation["completion"]["collisions"]
    safety.append({"evaluation": name, "completion": evaluation["completion"], "collisions": collisions})
(out / "raw-inventory.json").write_bytes((json.dumps(inventory, indent=2) + "\n").encode())
(out / "local-only-large-traces.json").write_bytes((json.dumps(local_only, indent=2) + "\n").encode())
(out / "safety-summary.json").write_bytes((json.dumps(safety, indent=2) + "\n").encode())
(out / "arithmetic-check.json").write_bytes((json.dumps({"verified": True, "updates": 1,
    "gradient_scalar": -3798, "a": row["a"], "final_rule": "max(0, initial - ak * gradient)",
    "roundoff_vs_simplified_initial_plus_delta": roundoff, "integer_counts_match_simplified_rule": True,
    "native_evaluations": 4, "reused_initial": 1, "new_native_evaluations": 3,
    "final_loss": result["evaluations"][-1]["loss"], "formal_result": False}, indent=2) + "\n").encode())
print("Verified all four native E1 comparisons and retained all 130/40 rows. No new simulation.")
