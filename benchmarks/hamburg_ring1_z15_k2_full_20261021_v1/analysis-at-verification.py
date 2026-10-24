"""Verify the complete observation rows retained after the tail execution timeout."""

from collections import defaultdict
import csv
import hashlib
import json
from pathlib import Path
import xml.etree.ElementTree as ET


ROOT = Path(__file__).resolve().parents[1]
NATIVE = ROOT / "outputs/ring1-z15-k2-full-native-20261021-v1"
REPORT = ROOT / "outputs/ring1-z15-k2-full-report-20261021-v1"
PREPARED = ROOT / "outputs/ring1-z15-k2-prepared-20261021-v1"
OLD = ROOT / "benchmarks/hamburg_ring1_lane_section_hold_full_20261021_v1"


def rows(path):
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def key(r):
    return r["detector_id"], float(r["begin"]), float(r["end"])


source_hashes = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in NATIVE.iterdir() if p.is_file()}
command = json.loads((NATIVE / "command.json").read_bytes())
run = json.loads((NATIVE / "run.json").read_bytes())
result = json.loads((REPORT / "results.json").read_bytes())
assert command["status"] == run["native_status"] == result["execution_status"] == "timeout"
assert result["valid_run"] is False and result["formal_field_metrics"] is None
complete = []
try:
    for _, r in ET.iterparse(NATIVE / "field-counts.xml", events=("end",)):
        if r.tag == "interval":
            complete.append(dict(r.attrib))
        r.clear()
except ET.ParseError:
    pass
counts = {(r["id"], float(r["begin"]), float(r["end"])): int(r["nVehContrib"]) for r in complete}
assert len(counts) == len(complete) == 188
fields = rows(REPORT / "field-comparison.csv")
assert len(fields) == 130 and len({r["detector_id"] for r in fields}) == 13
assert all(counts[key(r)] == int(r["measured_nVehContrib"]) for r in fields)
assert (PREPARED / "expected-counts.csv").read_bytes() == (OLD / "inputs/expected-counts.csv").read_bytes()
stations = rows(REPORT / "station-comparison.csv")
groups = {"35822": ("0119", {"Z.11", "Z.12", "Z.13"}), "33023": ("0119", {"Z.4", "Z.5", "Z.15"}),
          "30666": ("0535", {"Z.5", "Z.6", "Z.7", "Z.8"}), "26585": ("0535", {"Z.4", "Z.9", "Z.14"})}
assert len(stations) == 40
for s in stations:
    node, assets = groups[s["station_stream_id"]]
    selected = [r for r in fields if r["node_id"] == node and r["asset_id"] in assets
                and float(r["begin"]) == float(s["begin"]) and float(r["end"]) == float(s["end"])]
    assert len(selected) == len(assets)
    assert sum(int(r["measured_nVehContrib"]) for r in selected) == int(s["measured_nVehContrib"])
    assert sum(int(r["expected_total"]) for r in selected) == int(s["expected_total"])
    assert s["independent_evidence"] == "False"
prefix = ET.parse(ROOT / "outputs/ring1-z15-k2-native-20261021-v1/field-counts.xml").getroot().findall("interval")
assert all(counts[r.get("id"), float(r.get("begin")), float(r.get("end"))] == int(r.get("nVehContrib")) for r in prefix)
last = result["last_complete_summary_snapshot"]["step"]
assert float(last["time"]) == 13645 and int(last["waiting"]) == 2373
assert int(last["collisions"]) == int(last["teleports"]) == 0
text = (REPORT / "README.md").read_text(encoding="utf-8")
assert "均非计划结束时的最终数量" in text and "初始化 1953" in text
assert "完整整数等式在仿真前成立" not in text and "停止检查每 0.1 秒" in text
by_field = defaultdict(list)
for r in fields:
    by_field[r["detector_id"]].append(r)
summary = {f: {"target": sum(int(r["expected_total"]) for r in rs),
               "native": sum(int(r["measured_nVehContrib"]) for r in rs),
               "residuals": [int(r["diff_nVehContrib_minus_expected"]) for r in sorted(rs, key=lambda r: float(r["begin"]))]} for f, rs in by_field.items()}
(REPORT / "field-residual-summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
checks = {"formal_result": False, "execution_terminal": True, "execution_status": "timeout",
    "last_complete_snapshot_s": 13645, "native_completion_established": False,
    "all_130_native_E1_counts_rechecked": True, "all_40_station_sums_rechecked": True,
    "stations_independent": False, "all_main_targets_unchanged": True,
    "all_52_initialization_counts_match_independent_prefix": True,
    "native_passage_identity_reconciliation_available": False,
    "full_driver_trace_available": False, "raw_native_files_unchanged": True}
assert all(hashlib.sha256((NATIVE / n).read_bytes()).hexdigest() == h for n, h in source_hashes.items())
(REPORT / "independent-checks.json").write_text(json.dumps(checks, indent=2) + "\n", encoding="utf-8")
print(json.dumps({"checks": checks, "Z15": summary["hh_0119_Z_15_29375"]}))
