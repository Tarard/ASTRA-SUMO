"""Freeze the completed corridor attempt and check each native count again."""

from pathlib import Path
import csv
import gzip
import hashlib
import json
import re
import shutil
import xml.etree.ElementTree as ET

from astra_sumo.core.hamburg_readme_results import summarize_hamburg_readme

root = Path.cwd()
source = root / "outputs/ring1-munich-corridor-calibration-20261022-v1"
case = root / "benchmarks/hamburg_ring1_munich_corridor_20261022_v1"
result = json.loads((source / "corridor-calibration-result.json").read_bytes())
assert result["feedback"] is None and not result["formal_result"]
native = source / "native"
run = json.loads((native / "run.json").read_bytes())
assert run["observer_complete"] and run["stall_monitor"]["status"] == "completed"
assert run["completion"]["collisions"] and not run["healthy"]
review = source / "report-reviewed"
stats = summarize_hamburg_readme(repository=root, prepared_dir=source / "inputs", native_dir=native,
    figure_dir=case / "figure-sources", output_dir=review)
text = (review / "README.md").read_text(encoding="utf-8")
assert "40 个其余 OSM 信号" in text and "58 个其余 OSM 信号" not in text
assert "原参考路口保留与走廊裁剪" in text and "相同物理宽度" in text
for target in re.findall(r"\]\(([^)]+)\)", text):
    if not target.startswith(("http:", "https:")):
        assert (review / target).exists(), target
fields = list(csv.DictReader((review / "field-comparison.csv").open(encoding="utf-8", newline="")))
stations = list(csv.DictReader((review / "station-comparison.csv").open(encoding="utf-8", newline="")))
periodic = {(r.get("id"), float(r.get("begin")), float(r.get("end"))): int(r.get("nVehContrib"))
            for r in ET.parse(native / "field-counts.xml").getroot().findall("interval")}
assert len(fields) == 130 and len(stations) == 40 and not stats["missing_field_rows"]
for row in fields:
    key = row["detector_id"], float(row["begin"]), float(row["end"])
    assert int(row["measured_nVehContrib"]) == periodic[key]
assert stats["diagnostic_field_MAE"] == result["seed"]["diagnostic_field_MAE"]

def sha(path):
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()

def write_json(path, data):
    path.write_bytes((json.dumps(data, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))

out = case / "seed"
shutil.copytree(review, out)
write_json(case / "corridor-calibration-result.json", result)
execution = source.with_name(source.name + "-execution") / "execution.json"
shutil.copyfile(execution, case / "execution.json")
write_json(case / "report-generation.json", {"source_directory": str(source), "new_simulations": 0,
    "native_source_commit": json.loads(execution.read_bytes())["source_commit"],
    "analysis_file": "plugins/astra-sumo/src/astra_sumo/core/hamburg_readme_results.py",
    "analysis_sha256": sha(root / "plugins/astra-sumo/src/astra_sumo/core/hamburg_readme_results.py"),
    "checks": "All 130 nVehContrib rows, 40 station rows, controller count, figure labels and local report links verified."})
raw = case / "native"
raw.mkdir()
names = ("run.json", "command.json", "native-request.json", "field-counts.xml", "instant-e1.xml",
         "summary.xml", "tripinfo.xml", "vehicle-routes.xml", "collisions.xml", "stall-monitor.json",
         "sumo.log", "sumo-errors.log")
for name in names:
    path = native / name
    if path.exists():
        (raw / (name + ".gz")).write_bytes(gzip.compress(path.read_bytes(), mtime=0))
write_json(case / "raw-native-inventory.json", {"source_directory": str(native),
    "files": [{"path": p.name, "bytes": p.stat().st_size, "sha256": sha(p)}
              for p in sorted(native.iterdir()) if p.is_file()]})
for name in ("passage-reconciliation.json", "vehicle-field-passages.csv"):
    p = out / name
    (out / (name + ".gz")).write_bytes(gzip.compress(p.read_bytes(), mtime=0))
    p.unlink()
text = (out / "README.md").read_text(encoding="utf-8")
for name in ("passage-reconciliation.json", "vehicle-field-passages.csv"):
    text = text.replace(f"]({name})", f"]({name}.gz)")
text = text.replace("[run.json](../native/run.json)", "[run.json.gz](../native/run.json.gz)")
text = text.replace("[run_hamburg_readme_pipeline.py](../../../plugins/astra-sumo/scripts/run_hamburg_readme_pipeline.py)",
                    "[run-calibration.py](../run-calibration.py)")
(out / "README.md").write_bytes(text.encode("utf-8"))
for target in re.findall(r"\]\(([^)]+)\)", text):
    if not target.startswith(("http:", "https:")):
        assert (out / target).exists(), target
for source_name, target_name in [
    ("ring1-munich-corridor-first-collision-correspondence-20261022-v1.json", "internal-path-correspondence.json"),
    ("ring1-munich-corridor-demand-path-check-20261022-v1.json", "demand-path-check.json")]:
    shutil.copyfile(root / "outputs" / source_name, case / target_name)
shutil.copyfile(Path(__file__), case / "freeze-results.py")
manifest = json.loads((case / "manifest.json").read_bytes())
manifest.update(native_count_result="complete_invalid_collision_and_backlog", formal_result=False,
    actual_native_runs=1, further_feedback_runs=0, native_source_commit=json.loads(execution.read_bytes())["source_commit"])
manifest["files"] = [{"path": p.relative_to(case).as_posix(), "sha256": sha(p)}
    for p in sorted(case.rglob("*")) if p.is_file() and p != case / "manifest.json"]
write_json(case / "manifest.json", manifest)
print(json.dumps(stats, ensure_ascii=False, indent=2))
print("All counts, frozen report links and source hashes checked.")
