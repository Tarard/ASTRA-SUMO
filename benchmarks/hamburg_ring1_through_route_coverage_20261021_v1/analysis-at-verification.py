"""Verify the route-filter correction and retain the unsafe larger-demand run."""

from collections import Counter
from copy import deepcopy
import gzip
import json
from pathlib import Path
import xml.etree.ElementTree as ET

from astra_sumo.core.detector_demand import read_csv_rows, write_csv
from astra_sumo.core.hamburg_departure_probe import single_pass_structure


ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "benchmarks/hamburg_ring1_primary_exit_prior_20261021_v1"
PREPARED = ROOT / "outputs/ring1-through-prior-prepared-20261021-v2"
PROBE = ROOT / "outputs/ring1-through-routes-paired-probe-20261021-v1"
NATIVE = ROOT / "outputs/ring1-through-prior-native-20261021-v1"
OUT = ROOT / "outputs/ring1-through-route-analysis-20261021-v1"
OUT.mkdir(exist_ok=False)


def read(folder, name):
    p = folder / name
    if not p.exists():
        p = p.with_name(p.name + ".gz")
    data = p.read_bytes()
    return gzip.decompress(data) if p.suffix == ".gz" else data


def save(name, data):
    (OUT / name).write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


for n in ("network.net.xml", "control.add.xml", "field-loops.add.xml", "expected-counts.csv", "initialization-counts.csv"):
    assert read(BASE / "inputs", n) == read(PREPARED, n)
old = {v.get("id"): v for v in ET.fromstring(read(BASE / "inputs", "demand.rou.xml")).findall("vehicle")}
new = {v.get("id"): v for v in ET.parse(PREPARED / "demand.rou.xml").getroot().findall("vehicle")}
meta = {r["vehicle_id"]: r for r in json.loads(read(PREPARED, "lane-intentions.json"))["vehicles"]}
original_meta = {r["vehicle_id"]: r for r in json.loads(read(BASE / "inputs", "lane-intentions.json"))["vehicles"]}
assert old.keys() == new.keys() == meta.keys() and len(new) == 8640
changed = Counter()
for identity, a in old.items():
    b = deepcopy(new[identity])
    assert a.attrib == b.attrib
    if a.find("route").get("edges") != b.find("route").get("edges"):
        assert meta[identity]["profile_index"] in {106, 182, 183}
        b.find("route").set("edges", a.find("route").get("edges"))
        changed[meta[identity]["population_role"]] += 1
    assert ET.tostring(a) == ET.tostring(b)
    assert {k: v for k, v in meta[identity].items() if k != "profile_index"} == {k: v for k, v in original_meta[identity].items() if k != "profile_index"}
assert changed == {"main": 1126, "initialization": 425}
rules = {}
network = ET.parse(PREPARED / "network.net.xml").getroot()
loops = ET.parse(PREPARED / "field-loops.add.xml").getroot()
for label in ("baseline", "through"):
    rules[label] = single_pass_structure(network, ET.parse(PROBE / label / "demand.rou.xml").getroot(), loops)
assert rules["baseline"]["one_observed_road_per_route"]
assert not rules["through"]["one_observed_road_per_route"] and rules["through"]["structural_conditions_satisfied"]
save("rule-comparison.json", {"cases": rules, "claim_boundary": "Conditional complete-passage upper bound, not natural driver behavior or safety proof. The larger run violates the no-collision/no-teleport premise."})
probe = json.loads(read(PROBE, "probe-check.json"))
assert all(r["traffic_complete"] and r["declared_signature_matched"] and r["E1_reconciled"] for r in probe["results"].values())
before = read_csv_rows(BASE / "native/field-comparison.csv")
after = read_csv_rows(NATIVE / "field-comparison.csv")
def key(r):
    return r["detector_id"], float(r["begin"]), float(r["end"])
indexed = {key(r): r for r in before}
raw = {(r.get("id"), float(r.get("begin")), float(r.get("end"))): int(r.get("nVehContrib")) for r in ET.parse(NATIVE / "field-counts.xml").getroot().findall("interval")}
assert len(raw) == len(indexed) == len(after) == 52
comparison = []
for r in after:
    b = indexed[key(r)]
    assert r["expected_total"] == b["expected_total"] and raw[key(r)] == int(r["measured_nVehContrib"])
    comparison.append({**r, "baseline_native_nVehContrib": b["measured_nVehContrib"],
        "native_count_change": int(r["measured_nVehContrib"]) - int(b["measured_nVehContrib"]),
        "absolute_error_change": abs(int(r["diff_nVehContrib_minus_expected"])) - abs(int(b["diff_nVehContrib_minus_expected"]))})
write_csv(OUT / "field-change.csv", comparison, list(comparison[0]))
collisions = []
for event in ET.parse(NATIVE / "collisions.xml").getroot().findall("collision"):
    data = dict(event.attrib)
    data["junction_id"] = event.get("lane")[1:].rsplit("_", 2)[0]
    data["planned_profiles"] = {k: {"profile_index": meta[event.get(k)]["profile_index"], "assigned_field": meta[event.get(k)]["field_id"]} for k in ("collider", "victim")}
    collisions.append(data)
assert len(collisions) == 2 and {r["junction_id"] for r in collisions} == {"14398547", "205069138"}
save("collision-context.json", {"events": collisions, "cause_verified": False,
    "claim_boundary": "Native event locations and planned profiles only. The check stage and the planned route do not identify each vehicle's actual maneuver or a unique collision cause."})
run = json.loads(read(NATIVE, "initialization-check.json"))
assert run["completion"]["collisions"] == run["completion"]["teleports"] == 2 and not run["native_safe_prefix"]
passage = json.loads(read(NATIVE, "passage-reconciliation.json"))
assert passage["reconciled"]
counts = Counter((r["vehicle_id"], r["detector_id"]) for r in passage["passages"])
extra = [r for r in passage["passages"] if r["detector_id"] != meta[r["vehicle_id"]]["field_id"]]
save("independent-checks.json", {"all_8640_requests_types_stops_roles_target_bins_and_field_offsets_retained": True,
    "changed_routes_by_population": dict(changed), "network_controls_lane_permissions_real_targets_unchanged": True,
    "all_52_native_E1_counts_rechecked": True, "E1_reconciled": True, "other_complete_field_passages": len(extra),
    "repeated_same_field_passages": sum(max(0, n - 1) for n in counts.values()),
    "larger_run_valid": False, "conditional_bound_applicable_to_larger_run": False, "main_window_run": False, "formal_result": False})
save("comparison.json", {"baseline": json.loads(read(BASE / "native", "initialization-check.json")), "candidate": run,
    "improved_bins": sum(r["absolute_error_change"] < 0 for r in comparison), "worsened_bins": sum(r["absolute_error_change"] > 0 for r in comparison),
    "unchanged_bins": sum(r["absolute_error_change"] == 0 for r in comparison),
    "candidate_accepted": False, "formal_result": False, "reason": "Two collisions and two teleports. Lower diagnostic errors are not a valid calibration result."})
save("source-semantics.json", {"version": "1.27.1", "references": [
    {"url": "https://raw.githubusercontent.com/eclipse-sumo/sumo/v1_27_1/src/microsim/output/MSInductLoop.cpp", "sections": "notifyMove; notifyLeave; writeXMLOutput"},
    {"url": "https://raw.githubusercontent.com/eclipse-sumo/sumo/v1_27_1/src/microsim/output/MSInstantInductLoop.cpp", "sections": "notifyLeave"}],
    "inference": "Near-end complete point-loop passages require a compatible exit from the detector lane. The upper bound also needs fixed forward routes, instantaneous lane changes, no repeated roads, no collision, teleport or relocation.",
    "claim_boundary": "Conditional topology/measurement inference only, not field truth or calibration feasibility."})
print(json.dumps({"run_valid": False, "collision_junctions": [r["junction_id"] for r in collisions], "observed_additional_fields": len(extra)}))
