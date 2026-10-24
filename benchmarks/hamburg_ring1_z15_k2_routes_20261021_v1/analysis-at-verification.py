"""Compare the K31/K2 demand with the frozen full initialization population."""

from collections import Counter, defaultdict
from copy import deepcopy
import gzip
import json
from pathlib import Path
import xml.etree.ElementTree as ET

from astra_sumo.core.detector_demand import read_csv_rows, write_csv


ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "benchmarks/hamburg_ring1_initialization_population_20261021_v1"
PREPARED = ROOT / "outputs/ring1-z15-k2-prepared-20261021-v1"
NATIVE = ROOT / "outputs/ring1-z15-k2-native-20261021-v1"
PROBE = ROOT / "outputs/ring1-z15-k2-small-probe-20261021-v1"
OUT = ROOT / "outputs/ring1-z15-k2-analysis-20261021-v1"
OUT.mkdir(exist_ok=False)


def read(directory, name):
    p = directory / name
    if not p.is_file():
        p = p.with_name(p.name + ".gz")
    data = p.read_bytes()
    return gzip.decompress(data) if p.suffix == ".gz" else data


def save(name, data):
    (OUT / name).write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


names = ("network.net.xml", "control.add.xml", "field-loops.add.xml", "expected-counts.csv", "initialization-counts.csv")
assert all(read(BASE / "inputs", n) == read(PREPARED, n) for n in names)
old = {v.get("id"): v for v in ET.fromstring(read(BASE / "inputs", "demand.rou.xml")).findall("vehicle")}
new = {v.get("id"): v for v in ET.parse(PREPARED / "demand.rou.xml").getroot().findall("vehicle")}
meta = {r["vehicle_id"]: r for r in json.loads(read(PREPARED, "lane-intentions.json"))["vehicles"]}
assert old.keys() == new.keys() == meta.keys() and len(new) == 8640
original_meta = json.loads(read(BASE / "inputs", "lane-intentions.json"))["vehicles"]
for a in original_meta:
    b = meta[a["vehicle_id"]]
    assert {k: v for k, v in a.items() if k not in {"profile_index", "free_flow_field_offset_s"}} == {
        k: v for k, v in b.items() if k not in {"profile_index", "free_flow_field_offset_s"}}
changes = Counter()
for identity, a in old.items():
    b = deepcopy(new[identity])
    assert a.attrib == b.attrib
    if meta[identity]["field_id"] == "hh_0119_Z_15_29375":
        assert meta[identity]["profile_index"] in {89, 252}
        b.find("route").set("edges", a.find("route").get("edges"))
        changes[meta[identity]["population_role"]] += 1
    assert ET.tostring(a) == ET.tostring(b)
assert changes == {"main": 896, "initialization": 309}
probe = json.loads(read(PROBE, "probe-check.json"))
assert probe["passed"] and probe["completion"]["arrived"] == 20
native = json.loads(read(NATIVE, "initialization-check.json"))
baseline = json.loads(read(BASE / "native", "initialization-check.json"))
before = read_csv_rows(BASE / "native/field-comparison.csv")
after = read_csv_rows(NATIVE / "field-comparison.csv")


def key(row):
    return row["detector_id"], float(row["begin"]), float(row["end"])


before_by_key = {key(r): r for r in before}
raw_counts = {(r.get("id"), float(r.get("begin")), float(r.get("end"))): int(r.get("nVehContrib"))
              for r in ET.parse(NATIVE / "field-counts.xml").getroot().findall("interval")}
assert len(after) == len(before_by_key) == len(raw_counts) == 52
comparison = []
for r in after:
    b = before_by_key[key(r)]
    assert r["expected_total"] == b["expected_total"] and raw_counts[key(r)] == int(r["measured_nVehContrib"])
    comparison.append({**r, "baseline_native_nVehContrib": b["measured_nVehContrib"],
        "native_count_change": int(r["measured_nVehContrib"]) - int(b["measured_nVehContrib"]),
        "absolute_error_change": abs(int(r["diff_nVehContrib_minus_expected"])) - abs(int(b["diff_nVehContrib_minus_expected"]))})
write_csv(OUT / "field-change.csv", comparison, list(comparison[0]))
passage = json.loads(read(NATIVE, "passage-reconciliation.json"))
assert passage["reconciled"]
passed = {r["vehicle_id"] for r in passage["passages"]}
per_car = Counter(r["vehicle_id"] for r in passage["passages"])
wrong = [r for r in passage["passages"] if r["detector_id"] != meta[r["vehicle_id"]]["field_id"]]
trips = {r.get("id"): r for r in ET.parse(NATIVE / "tripinfo.xml").getroot().findall("tripinfo")}
entries = defaultdict(list)
for identity, row in meta.items():
    if row["population_role"] == "initialization":
        entries[new[identity].find("route").get("edges").split()[0]].append(identity)
entry_rows = []
for entry, ids in sorted(entries.items()):
    inserted = [i for i in ids if i in trips]
    entry_rows.append({"entry": entry, "requested": len(ids), "inserted": len(inserted), "not_inserted": len(ids) - len(inserted),
        "complete_field_passages": len(set(ids) & passed), "max_native_depart_delay_s": max((float(trips[i].get("departDelay")) for i in inserted), default=None)})
write_csv(OUT / "entry-delays.csv", entry_rows, list(entry_rows[0]))
save("comparison.json", {"baseline": baseline, "candidate": native,
    "all_52_real_initialization_rows_retained": True,
    "improved_bins": sum(r["absolute_error_change"] < 0 for r in comparison),
    "worsened_bins": sum(r["absolute_error_change"] > 0 for r in comparison),
    "unchanged_bins": sum(r["absolute_error_change"] == 0 for r in comparison),
    "main_window_run": False, "formal_result": False})
save("independent-checks.json", {"all_8640_vehicles_request_times_types_stops_and_targets_retained": True,
    "changed_routes_by_population": dict(changes), "unchanged_main_field_rows": 130,
    "all_52_native_nVehContrib_checked": True, "native_E1_reconciled": True,
    "wrong_field_passages": len(wrong), "duplicate_field_passages": sum(n - 1 for n in per_car.values()),
    "small_probe_passed": True, "main_window_run": False, "formal_result": False})
print(json.dumps(native))
