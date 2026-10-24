"""Verify the single-entry candidate against the complete frozen initialization hour."""

from collections import Counter, defaultdict
import gzip
import hashlib
import json
from pathlib import Path
import xml.etree.ElementTree as ET

from astra_sumo.core.detector_demand import read_csv_rows, write_csv
from astra_sumo.core.hamburg_readme_results import vehicle_field_passage_rows


ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "benchmarks/hamburg_ring1_initialization_population_20261021_v1"
PREPARED = ROOT / "outputs/ring1-ballindamm-entry-prepared-20261021-v1"
NATIVE = ROOT / "outputs/ring1-ballindamm-entry-native-20261021-v1"
OUT = ROOT / "outputs/ring1-ballindamm-entry-analysis-20261021-v2"
OUT.mkdir(exist_ok=False)


def read(directory, name):
    path = directory / name
    if not path.is_file():
        path = path.with_name(path.name + ".gz")
    data = path.read_bytes()
    return gzip.decompress(data) if path.suffix == ".gz" else data


def save(name, value):
    (OUT / name).write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


unchanged = ["network.net.xml", "control.add.xml", "field-loops.add.xml", "expected-counts.csv", "initialization-counts.csv"]
assert all(read(BASE / "inputs", name) == read(PREPARED, name) for name in unchanged)
old = {v.get("id"): v for v in ET.fromstring(read(BASE / "inputs", "demand.rou.xml")).findall("vehicle")}
new = {v.get("id"): v for v in ET.parse(PREPARED / "demand.rou.xml").getroot().findall("vehicle")}
intent = {r["vehicle_id"]: r for r in json.loads(read(PREPARED, "lane-intentions.json"))["vehicles"]}
assert old.keys() == new.keys() == intent.keys() and len(new) == 8640
changes, due_bins, sources = Counter(), Counter(), defaultdict(list)
for identity, before in old.items():
    after = new[identity]
    assert before.attrib == after.attrib
    assert [ET.tostring(s) for s in before.findall("stop")] == [ET.tostring(s) for s in after.findall("stop")]
    a, b = before.find("route").get("edges").split(), after.find("route").get("edges").split()
    if a != b:
        assert a[0] == "543550497#0" and b[0] == "551288837#2"
        assert a[a.index("186714394"):] == b[b.index("186714394"):]
        changes[intent[identity]["population_role"]] += 1
    sources[b[0]].append(identity)
    if b[0] == "551288837#2":
        due_bins[int(float(after.get("depart")) // 900)] += 1
assert changes == {"initialization": 307, "main": 890}
assert max(due_bins.values()) == 106 <= 150
baseline_intent = {r["vehicle_id"]: r for r in json.loads(read(BASE / "inputs", "lane-intentions.json"))["vehicles"]}
for identity, r in intent.items():
    assert {k: v for k, v in r.items() if k not in {"profile_index", "free_flow_field_offset_s"}} == {
        k: v for k, v in baseline_intent[identity].items() if k not in {"profile_index", "free_flow_field_offset_s"}}

comparison = read_csv_rows(NATIVE / "field-comparison.csv")
baseline_rows = read_csv_rows(BASE / "native/field-comparison.csv")
def keys(r):
    return r["detector_id"], float(r["begin"]), float(r["end"])


before_counts = {keys(r): r for r in baseline_rows}
raw = {(r.get("id"), float(r.get("begin")), float(r.get("end"))): int(r.get("nVehContrib"))
       for r in ET.parse(NATIVE / "field-counts.xml").getroot().findall("interval")}
assert len(comparison) == len(before_counts) == len(raw) == 52
assert all(raw[keys(r)] == int(r["measured_nVehContrib"]) for r in comparison)
comparison_rows = [{**r, "baseline_native_nVehContrib": before_counts[keys(r)]["measured_nVehContrib"],
    "native_count_change": int(r["measured_nVehContrib"]) - int(before_counts[keys(r)]["measured_nVehContrib"])} for r in comparison]
write_csv(OUT / "field-change.csv", comparison_rows, list(comparison_rows[0]))
passage = json.loads(read(NATIVE, "passage-reconciliation.json"))
assert passage["reconciled"]
passed = {r["vehicle_id"] for r in passage["passages"]}
wrong = [r for r in passage["passages"] if r["detector_id"] != intent[r["vehicle_id"]]["field_id"]]
counts = Counter(r["vehicle_id"] for r in passage["passages"])
trips = {r.get("id"): r for r in ET.parse(NATIVE / "tripinfo.xml").getroot().findall("tripinfo")}
rows = []
for entry, all_ids in sorted(sources.items()):
    ids = [i for i in all_ids if intent[i]["population_role"] == "initialization"]
    inserted = [i for i in ids if i in trips]
    rows.append({"entry": entry, "requested_initialization": len(ids), "inserted": len(inserted),
        "not_inserted": len(ids) - len(inserted), "complete_field_passages": len(set(ids) & passed),
        "max_native_depart_delay_s": max((float(trips[i].get("departDelay")) for i in inserted), default=None)})
write_csv(OUT / "entry-delays.csv", rows, list(rows[0]))
vehicle_rows = vehicle_field_passage_rows(list(intent.values()), passage)
write_csv(OUT / "vehicle-field-passages.csv", vehicle_rows, list(vehicle_rows[0]))
before_report = json.loads(read(BASE / "native", "initialization-check.json"))
after_report = json.loads(read(NATIVE, "initialization-check.json"))
monitor = json.loads(read(NATIVE, "stall-monitor.json"))
state = ET.fromstring(read(NATIVE / "states", "state_3599.900.xml.gz"))
state_vehicles = {v.get("id"): v for v in state.findall("vehicle")}
stock_rows = []
for lane in state.findall("lane"):
    ids = lane.find("vehicles").get("value").split()
    pending = [i for i in ids if intent[i]["field_id"] == "hh_0119_Z_15_29375" and i not in passed]
    if pending:
        lead = max(ids, key=lambda i: float(state_vehicles[i].get("pos").split()[0]))
        stock_rows.append({"lane_id": lane.get("id"), "Z15_before_complete_passage": len(pending),
            "all_lane_vehicles": len(ids), "furthest_front_vehicle": lead,
            "furthest_front_position_m": float(state_vehicles[lead].get("pos").split()[0]),
            "furthest_front_speed_m_per_s": float(state_vehicles[lead].get("speed").split()[0])})
save("Z15-stock-lanes.json", {"native_time_s": float(state.get("time")), "rows": stock_rows,
    "claim_boundary": "One native stock snapshot. Lane occupancy does not identify a causal bottleneck, signal truth, or field queue length."})
save("independent-checks.json", {"all_8640_ids_request_times_stops_and_target_bins_retained": True,
    "changed_entry_by_population": dict(changes), "unchanged_sha256": {n: hashlib.sha256(read(PREPARED, n)).hexdigest() for n in unchanged},
    "replacement_entry_max_requested_vehicles_per_900s": max(due_bins.values()), "declared_prior_per_900s": 150,
    "all_52_native_counts_checked": True, "native_E1_reconciled": True,
    "wrong_field_passages": len(wrong), "duplicate_field_passages": sum(n - 1 for n in counts.values()),
    "lane_holds_released": monitor["lane_section_hold"]["released_vehicles"],
    "lane_holds_left_before_release": monitor["lane_section_hold"]["left_before_release"],
    "emergency_braking_warnings": (NATIVE / "sumo-errors.log").read_text(encoding="utf-8").count("performs emergency braking"),
    "main_window_run": False, "formal_result": False})
save("comparison.json", {"baseline": before_report, "candidate": after_report,
    "native_count_change": sum(int(r["native_count_change"]) for r in comparison_rows),
    "claim_boundary": "One preceding-hour entry-prefix comparison. All 52 initialization bins retained. The original main 130/40 targets are unchanged and untested in this run. Road class and declared entry priors are not observed OD or capacity."})
print(json.dumps(after_report))
