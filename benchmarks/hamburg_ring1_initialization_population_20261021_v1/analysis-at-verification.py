"""Check this initialization candidate and its ordinary-Monday source schedule."""

from collections import Counter, defaultdict
import gzip
import hashlib
import json
from pathlib import Path
import xml.etree.ElementTree as ET

from astra_sumo.core.detector_demand import read_csv_rows, write_csv
from astra_sumo.core.hamburg_readme_results import vehicle_field_passage_rows


ROOT = Path(__file__).resolve().parents[1]
PREPARED = ROOT / "outputs/ring1-initialization-population-prepared-20261021-v1"
NATIVE = ROOT / "outputs/ring1-initialization-population-native-20261021-v1"
OUT = ROOT / "outputs/ring1-initialization-population-analysis-20261021-v2"
OUT.mkdir(exist_ok=False)


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def save(name, value):
    (OUT / name).write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


schedule = []
for node in ("104", "118", "119", "200", "535"):
    path = ROOT / f"benchmarks/hamburg_ring1_official_sources_20261020_v1/{node}-ocit_xml.xml.gz"
    source = ET.fromstring(gzip.decompress(path.read_bytes()))
    monday = source.findtext(".//StandardWochenplan/WochenplanListe/Tagesplan_Mo")
    days = [r for r in source.iter() if r.tag in {"StandardTagesplan", "Tagesplan"}
            and r.findtext("OCITOutstationNr") == monday and r.findall("Befehl")]
    assert len(days) == 1
    commands = [{k: r.findtext(k) for k in ("Uhrzeit", "Programm", "KnotenEinAus", "VA")}
                for r in days[0].findall("Befehl")]
    commands.sort(key=lambda r: r["Uhrzeit"])
    active = [r for r in commands if r["Uhrzeit"] <= "06:30:00"][-1]
    following = [r for r in commands if r["Uhrzeit"] > "06:30:00"][0]
    assert following["Uhrzeit"] >= "10:00:00"
    expected = {"104": ("1", "true"), "118": ("1", "true"), "119": ("1", "false"),
                "200": ("5", "false"), "535": ("1", "false")}[node]
    assert (active["Programm"], active["VA"]) == expected
    schedule.append({"node": node, "source_sha256": sha(path), "source_monday_day_plan": monday,
        "active_ordinary_monday_command": active, "next_command": following,
        "installed_fixed_program_reference": "5" if node in {"104", "118"} else active["Programm"],
        "fixed_approximation_of_source_actuation": node in {"104", "118"}})
save("source-schedule-check.json", {"local_interval": "2026-10-19 06:30–10:00 CEST", "rows": schedule,
    "claim_boundary": "Ordinary-Monday source commands only. Not a same-window field signal replay. 104/118 fixed SP5 approximate actuated SP1. Earlier than 06:30 needs another 535 program. No general annual calendar selector was added."})

source = ROOT / "benchmarks/hamburg_ring1_lane_section_hold_full_20261021_v1/inputs"
original = ET.fromstring(gzip.decompress((source / "demand.rou.xml.gz").read_bytes()))
demand = ET.parse(PREPARED / "demand.rou.xml").getroot()
original_by_id = {v.get("id"): ET.tostring(v) for v in original.findall("vehicle")}
current_by_id = {v.get("id"): v for v in demand.findall("vehicle")}
assert all(ET.tostring(current_by_id[k]) == v for k, v in original_by_id.items())
unchanged = ["network.net.xml", "control.add.xml", "field-loops.add.xml", "expected-counts.csv"]
assert all((source / name).read_bytes() == (PREPARED / name).read_bytes() for name in unchanged)
assert len(current_by_id) == 8640 and len(original_by_id) == 6687
intent = {r["vehicle_id"]: r for r in json.loads((PREPARED / "lane-intentions.json").read_bytes())["vehicles"]}
assert intent.keys() == current_by_id.keys()
assert Counter(r["population_role"] for r in intent.values()) == {"main": 6687, "initialization": 1953}
assert all(float(current_by_id[k].get("depart")) < 3600 for k, r in intent.items() if r["population_role"] == "initialization")
counts = read_csv_rows(PREPARED / "initialization-counts.csv")
planned = Counter((r["field_id"], r["target_begin_s"]) for r in intent.values() if r["population_role"] == "initialization")
assert all(planned[r["detector_id"], float(r["begin"])] == int(r["expected_total"]) for r in counts)
assert len(read_csv_rows(PREPARED / "expected-counts.csv")) == 130

run = json.loads((NATIVE / "run.json").read_bytes())
passage = json.loads((NATIVE / "passage-reconciliation.json").read_bytes())
assert passage["reconciled"]
raw_counts = {(r.get("id"), float(r.get("begin")), float(r.get("end"))): int(r.get("nVehContrib"))
              for r in ET.parse(NATIVE / "field-counts.xml").getroot().findall("interval")}
comparison = read_csv_rows(NATIVE / "field-comparison.csv")
assert len(comparison) == len(raw_counts) == 52
assert all(raw_counts[r["detector_id"], float(r["begin"]), float(r["end"])] == int(r["measured_nVehContrib"]) for r in comparison)
trips = {r.get("id"): r for r in ET.parse(NATIVE / "tripinfo.xml").getroot().findall("tripinfo")}
passed = {r["vehicle_id"] for r in passage["passages"]}
wrong = [r for r in passage["passages"] if r["detector_id"] != intent[r["vehicle_id"]]["field_id"]]
per_vehicle = Counter(r["vehicle_id"] for r in passage["passages"])
entries = defaultdict(list)
for identity, row in intent.items():
    if row["population_role"] == "initialization":
        entries[current_by_id[identity].find("route").get("edges").split()[0]].append(identity)
entry_rows = []
for entry, ids in sorted(entries.items()):
    inserted = [i for i in ids if i in trips]
    entry_rows.append({"entry": entry, "requested": len(ids), "inserted": len(inserted),
        "not_inserted": len(ids) - len(inserted), "complete_field_passages": len(set(ids) & passed),
        "inserted_without_complete_field_passage": len(set(inserted) - passed),
        "max_native_depart_delay_s": max((float(trips[i].get("departDelay")) for i in inserted), default=None)})
write_csv(OUT / "initialization-entry-delays.csv", entry_rows, list(entry_rows[0]))
stock = [i for i, t in trips.items() if float(t.get("arrival")) < 0]
monitor = json.loads((NATIVE / "stall-monitor.json").read_bytes())
hold = monitor["lane_section_hold"]
vehicle_rows = vehicle_field_passage_rows(list(intent.values()), passage)
write_csv(OUT / "vehicle-field-passages.csv", vehicle_rows, list(vehicle_rows[0]))
save("independent-checks.json", {"all_6687_main_vehicles_unchanged": True, "all_130_main_targets_byte_identical": True,
    "unchanged_files_sha256": {n: sha(PREPARED / n) for n in unchanged}, "model_initialization_vehicles": 1953,
    "all_52_initialization_proposal_totals_match_source": True, "all_52_native_field_counts_rechecked": True,
    "native_E1_reconciled": True, "unique_complete_passage_vehicles": len(passed),
    "wrong_field_passages": len(wrong), "duplicate_complete_passages": sum(n - 1 for n in per_vehicle.values()),
    "native_stock_at_end": len(stock), "stock_after_complete_field_passage": len(set(stock) & passed),
    "stock_without_complete_field_passage": len(set(stock) - passed), "main_window_run": False,
    "lane_holds_released": hold["released_vehicles"], "lane_holds_active_on_running_vehicles": len(hold["still_held"]),
    "lane_holds_left_before_release": hold["left_before_release"],
    "emergency_braking_warnings": (NATIVE / "sumo-errors.log").read_text(encoding="utf-8").count("performs emergency braking"),
    "source_schedule_checked": True, "native_completion": run["completion"],
    "claim_boundary": "The initialization comparison is diagnostic. No independent true OD, real distinct vehicle count, real initial stock, main-window fit, or checkpoint restart is established."})
print(json.dumps(json.loads((OUT / "independent-checks.json").read_bytes())))
