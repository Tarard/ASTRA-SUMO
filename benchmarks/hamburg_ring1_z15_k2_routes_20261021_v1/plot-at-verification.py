"""Draw every initialization-field error and the Z15 native count curves."""

from collections import defaultdict
import csv
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "benchmarks/hamburg_ring1_z15_k2_routes_20261021_v1"
with (OUT / "analysis/field-change.csv").open(encoding="utf-8", newline="") as handle:
    rows = list(csv.DictReader(handle))
groups = defaultdict(list)
for r in rows:
    groups[r["detector_id"]].append(r)
fields = sorted(groups)
fig, axes = plt.subplots(1, 2, figsize=(15, 5), constrained_layout=True)
z15 = sorted(groups["hh_0119_Z_15_29375"], key=lambda r: float(r["begin"]))
for key, label, color in [("expected_total", "Source counts", "#333333"),
                           ("baseline_native_nVehContrib", "Original K8 routes", "#c46531"),
                           ("measured_nVehContrib", "K31/K2 candidate", "#31688e")]:
    axes[0].plot(range(4), [int(r[key]) for r in z15], "o-", label=label, color=color)
axes[0].set(xticks=range(4), xticklabels=["06:30", "06:45", "07:00", "07:15"], xlabel="CEST, 19 Oct 2026", ylabel="Native E1 / source count per 15 min", title="Z15 | unchanged source targets")
axes[0].legend()
x = np.arange(len(fields))
for offset, key, label, color in [(-.18, "baseline_native_nVehContrib", "Original K8 routes", "#c46531"),
                                (.18, "measured_nVehContrib", "K31/K2 candidate", "#31688e")]:
    values = [sum(abs(int(r[key]) - int(r["expected_total"])) for r in groups[f]) for f in fields]
    axes[1].bar(x + offset, values, .36, label=label, color=color)
axes[1].set(xticks=x, xticklabels=[f.replace("hh_0", "").rsplit("_", 1)[0].replace("_Z_", " Z") for f in fields],
            ylabel="Sum of absolute bin errors", title="All 13 fields | all 52 initialization bins")
axes[1].tick_params(axis="x", rotation=65)
for ax in axes:
    ax.grid(axis="y", alpha=.2)
fig.suptitle("Initialization diagnostic only | main observation window assessed separately")
(OUT / "figures").mkdir(exist_ok=False)
fig.savefig(OUT / "figures/initialization-count-comparison.png", dpi=150)
plt.close(fig)
