"""Check the full selected population and draw the observed K8 cycle counts."""

from collections import Counter
import csv
import gzip
import hashlib
import json
from pathlib import Path
import xml.etree.ElementTree as ET

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "outputs/ring1-z15-route-delay-20261021-v3"
CASE = ROOT / "benchmarks/hamburg_ring1_ballindamm_entry_probe_20261021_v1"


def rows(name):
    with (OUT / name).open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


population = rows("population.csv")
intervals = rows("vehicle-road-intervals.csv")
arrivals = ET.fromstring(gzip.decompress((CASE / "native/vehicle-routes.xml.gz").read_bytes()))
arrival_time = {v.get("id"): float(v.get("arrival")) for v in arrivals.findall("vehicle")}
totals = Counter()
for r in intervals:
    totals[r["vehicle_id"]] += float(r["elapsed_s"])
for r in population:
    if r["status"] == "not_inserted":
        assert r["vehicle_id"] not in totals and r["insertion_wait_s"] == ""
    else:
        end = arrival_time[r["vehicle_id"]] if r["status"] == "arrived" else 3599.9
        assert abs(totals[r["vehicle_id"]] - (end - float(r["actual_depart_s"]))) < 1e-6
assert Counter(r["status"] for r in population) == {"arrived": 211, "running": 54, "not_inserted": 44}
crossing = [r for r in intervals if r["road"] == "186713403#0" and r["right_censored"] == "False"]
assert len(crossing) == 230 and all(35 <= float(r["end_s"]) % 90 < 40 for r in crossing)
cycles = rows("K8-exits-by-cycle.csv")
assert len(cycles) == 40 and all(int(r["native_front_exits"]) == 6 for r in cycles[2:])
report = json.loads((OUT / "report.json").read_bytes())
assert report["native_E1_count_0_3600"] == 222
assert 222 + report["after_K8_before_complete_field_passage"] + report["inserted_without_K8_front_exit"] + 44 == 309
for name, digest in report["source_sha256"].items():
    assert hashlib.sha256(Path(name).read_bytes()).hexdigest() == digest
(OUT / "independent-checks.json").write_text(json.dumps({"selected_population": 309,
    "all_inserted_vehicle_elapsed_time_identities_verified": True,
    "pending_requests_retained_without_zero_wait_imputation": 44,
    "population_status": dict(Counter(r["status"] for r in population)),
    "K8_front_exits": 230, "all_K8_front_exits_in_installed_green": True,
    "cycles_2_through_39_each_have_six_front_exits": True,
    "native_E1_complete_passages": 222, "after_K8_before_field": 8, "inserted_before_K8": 35,
    "all_source_files_unchanged": True, "new_simulations": 0,
    "capacity_bound_established": False, "main_calibration_established": False}, indent=2) + "\n", encoding="utf-8")
(OUT / "figures").mkdir(exist_ok=False)
fig, ax = plt.subplots(figsize=(11, 4), constrained_layout=True)
ax.bar([int(r["cycle"]) for r in cycles], [int(r["native_front_exits"]) for r in cycles], color="#31688e")
ax.set(xlabel="90 s cycle after 06:30 CEST", ylabel="Native road-front exits (diagnostic)",
       title="LSA200 K8 | 5 s green per 90 s | 38 successive cycles with 6 front exits", ylim=(0, 7))
ax.grid(axis="y", alpha=.2)
fig.savefig(OUT / "figures/K8-exits-per-cycle.png", dpi=160)
plt.close(fig)
print("Checked all 309 requests, 265 inserted elapsed-time identities, and the 230 K8 front exits.")
