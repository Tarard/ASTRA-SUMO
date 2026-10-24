"""Keep the complete paired comparison and the evidence behind the exit prior."""

from collections import Counter, defaultdict
from copy import deepcopy
import gzip
import json
from pathlib import Path
from statistics import median
import xml.etree.ElementTree as ET

from astra_sumo.core.detector_demand import read_csv_rows, write_csv
from astra_sumo.core.hamburg_route_delay import censored_route_intervals


ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "benchmarks/hamburg_ring1_z15_k2_routes_20261021_v1"
PREPARED = ROOT / "outputs/ring1-primary-exit-prepared-20261021-v1"
NATIVE = ROOT / "outputs/ring1-primary-exit-native-20261021-v1"
PROBE = ROOT / "outputs/ring1-primary-exit-paired-probe-20261021-v1"
OUT = ROOT / "outputs/ring1-primary-exit-analysis-20261021-v1"
OUT.mkdir(exist_ok=False)
PRIMARY = {"hh_0119_Z_11_28853", "hh_0119_Z_12_31546", "hh_0119_Z_13_34018"}


def read(folder, name):
    path = folder / name
    if not path.exists():
        path = path.with_name(path.name + ".gz")
    data = path.read_bytes()
    return gzip.decompress(data) if path.suffix == ".gz" else data


def save(name, value):
    (OUT / name).write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


for n in ("network.net.xml", "control.add.xml", "field-loops.add.xml", "expected-counts.csv", "initialization-counts.csv"):
    assert read(BASE / "inputs", n) == read(PREPARED, n)
old = {v.get("id"): v for v in ET.fromstring(read(BASE / "inputs", "demand.rou.xml")).findall("vehicle")}
new = {v.get("id"): v for v in ET.parse(PREPARED / "demand.rou.xml").getroot().findall("vehicle")}
meta = {r["vehicle_id"]: r for r in json.loads(read(PREPARED, "lane-intentions.json"))["vehicles"]}
old_meta = {r["vehicle_id"]: r for r in json.loads(read(BASE / "inputs", "lane-intentions.json"))["vehicles"]}
assert old.keys() == new.keys() == meta.keys() and len(new) == 8640
changes = Counter()
for identity, a in old.items():
    b = deepcopy(new[identity])
    assert a.attrib == b.attrib
    if a.find("route").get("edges") != b.find("route").get("edges"):
        assert meta[identity]["field_id"] in {"hh_0119_Z_12_31546", "hh_0119_Z_13_34018"}
        b.find("route").set("edges", a.find("route").get("edges"))
        changes[meta[identity]["population_role"]] += 1
    assert ET.tostring(a) == ET.tostring(b)
    assert {k: v for k, v in meta[identity].items() if k != "profile_index"} == {k: v for k, v in old_meta[identity].items() if k != "profile_index"}
assert changes == {"main": 2257, "initialization": 851}
before = read_csv_rows(BASE / "native/field-comparison.csv")
after = read_csv_rows(NATIVE / "field-comparison.csv")
index = {(r["detector_id"], float(r["begin"]), float(r["end"])): r for r in before}
raw = {(r.get("id"), float(r.get("begin")), float(r.get("end"))): int(r.get("nVehContrib")) for r in ET.parse(NATIVE / "field-counts.xml").getroot().findall("interval")}
assert len(raw) == len(after) == len(index) == 52
comparison = []
for r in after:
    key = r["detector_id"], float(r["begin"]), float(r["end"])
    b = index[key]
    assert raw[key] == int(r["measured_nVehContrib"]) and r["expected_total"] == b["expected_total"]
    comparison.append({**r, "baseline_native_nVehContrib": b["measured_nVehContrib"],
        "native_count_change": int(r["measured_nVehContrib"]) - int(b["measured_nVehContrib"]),
        "absolute_error_change": abs(int(r["diff_nVehContrib_minus_expected"])) - abs(int(b["diff_nVehContrib_minus_expected"]))})
write_csv(OUT / "field-change.csv", comparison, list(comparison[0]))
passage = json.loads(read(NATIVE, "passage-reconciliation.json"))
assert passage["reconciled"]
wrong = [r for r in passage["passages"] if r["detector_id"] != meta[r["vehicle_id"]]["field_id"]]
per_vehicle = Counter(r["vehicle_id"] for r in passage["passages"])
assert not wrong and max(per_vehicle.values()) == 1
trips = {r.get("id"): r for r in ET.parse(NATIVE / "tripinfo.xml").getroot().findall("tripinfo")}
entries = defaultdict(list)
for identity, row in meta.items():
    if row["population_role"] == "initialization":
        entries[new[identity].find("route").get("edges").split()[0]].append(identity)
entry_rows = [{"entry": e, "requested": len(ids), "inserted": sum(i in trips for i in ids),
               "not_inserted": sum(i not in trips for i in ids),
               "max_native_depart_delay_s": max((float(trips[i].get("departDelay")) for i in ids if i in trips), default=None)} for e, ids in sorted(entries.items())]
write_csv(OUT / "entry-delays.csv", entry_rows, list(entry_rows[0]))
diagnosis = {}
for label, inputs, native in [("baseline", BASE / "inputs", BASE / "native"), ("candidate", PREPARED, NATIVE)]:
    intents = {r["vehicle_id"]: r for r in json.loads(read(inputs, "lane-intentions.json"))["vehicles"]}
    selected = {i for i, r in intents.items() if r["population_role"] == "initialization" and r["field_id"] in PRIMARY}
    population, intervals = censored_route_intervals(demand=ET.fromstring(read(inputs, "demand.rou.xml")),
        arrived_routes=ET.fromstring(read(native, "vehicle-routes.xml")), snapshot=ET.fromstring(read(native, "states/state_3599.900.xml.gz")),
        trips=ET.fromstring(read(native, "tripinfo.xml")), selected_ids=selected)
    by_road = defaultdict(list)
    for r in intervals:
        by_road[r["road"]].append(r)
    summary = []
    for road, rs in by_road.items():
        completed = [r["elapsed_s"] for r in rs if not r["right_censored"]]
        summary.append({"road": road, "completed_intervals": len(completed), "censored_intervals": sum(r["right_censored"] for r in rs),
            "observed_elapsed_sum_s": sum(r["elapsed_s"] for r in rs), "median_completed_interval_s": median(completed) if completed else None})
    summary.sort(key=lambda r: r["observed_elapsed_sum_s"], reverse=True)
    write_csv(OUT / f"{label}-primary-road-intervals.csv", summary, list(summary[0]))
    hold = json.loads(read(native, "stall-monitor.json"))["lane_section_hold"]
    acquired = defaultdict(list)
    for e in hold["events"]:
        if e["event"] == "held" and e["vehicle_id"] in selected:
            acquired[intents[e["vehicle_id"]]["field_id"]].append(e["position_m"])
    diagnosis[label] = {"selected_requests": len(population), "population_status": dict(Counter(r["status"] for r in population)),
        "hold_acquisition": {f: {"events": len(xs), "median_position_m": median(xs),
            "above_one_step_distance": sum(x > 13.888889 * .1 + 1e-6 for x in xs)} for f, xs in acquired.items()}}
save("primary-diagnosis.json", {"cases": diagnosis, "one_step_distance_bound_m": 13.888889 * .1,
    "claim_boundary": "Completed and censored native intervals include incoming internal connections. One-step positions only bound late acquisition. Queue records and snapshots do not identify all causal mechanisms or field capacities."})
probe = json.loads(read(PROBE, "probe-check.json"))
assert probe["both_route_checks_passed"]
probe["destination_counts"] = {label: dict(Counter(v.find("route").get("edges").split()[-1] for v in ET.parse(PROBE / label / "demand.rou.xml").getroot().findall("vehicle"))) for label in ("baseline", "candidate")}
save("paired-probe-summary.json", probe)
save("comparison.json", {"baseline": json.loads(read(BASE / "native", "initialization-check.json")),
    "candidate": json.loads(read(NATIVE, "initialization-check.json")),
    "improved_bins": sum(r["absolute_error_change"] < 0 for r in comparison),
    "worsened_bins": sum(r["absolute_error_change"] > 0 for r in comparison),
    "unchanged_bins": sum(r["absolute_error_change"] == 0 for r in comparison),
    "formal_result": False, "main_window_run": False})
save("independent-checks.json", {"all_8640_ids_request_times_types_stops_targets_and_field_offsets_retained": True,
    "network_control_lane_permissions_and_real_targets_unchanged": True, "all_52_native_E1_counts_rechecked": True,
    "wrong_field_passages": 0, "duplicate_field_passages": 0, "main_window_run": False, "formal_result": False})
print(json.dumps({"changed_routes": dict(changes), "diagnosis": diagnosis, "probe_destinations": probe["destination_counts"]}))
