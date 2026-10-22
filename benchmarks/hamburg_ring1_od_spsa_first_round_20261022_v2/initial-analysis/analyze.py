"""Recompute initial OD metrics from immutable native E1 output."""

from collections import defaultdict
import json
from pathlib import Path
import xml.etree.ElementTree as ET

from astra_sumo.core.candidate_contracts import file_sha256
from astra_sumo.core.detector_demand import audit_expected_to_e1_strict, read_csv_rows, write_csv
from astra_sumo.core.hamburg_readme_results import STATIONS


OUT = Path(__file__).resolve().parent
ROOT = OUT.parents[1]
PREPARED = ROOT / "outputs/hamburg-od-baseline-20261022-v1"
CACHED = ROOT / "outputs/hamburg-od-initial-native-20261022-v1"
NATIVE = CACHED / "native"
sources = [PREPARED / "expected-counts.csv", PREPARED / "network.net.xml",
           PREPARED / "control.add.xml", PREPARED / "field-loops.add.xml",
           PREPARED / "od-model.json", CACHED / "od-vector.json", CACHED / "demand.rou.xml",
           CACHED / "lane-intentions.json"]
sources += [NATIVE / name for name in ("run.json", "field-counts.xml", "summary.xml", "tripinfo.xml",
                                      "collisions.xml", "stall-monitor.json", "command.json", "native-request.json")]
before = {str(path): file_sha256(path) for path in sources}
expected = read_csv_rows(PREPARED / "expected-counts.csv")
comparison = audit_expected_to_e1_strict(expected, NATIVE / "field-counts.xml", count_attribute="nVehContrib")
assert len(comparison) == len(expected) == 130
assert all(row["measured_nVehContrib"] is not None for row in comparison)
source_by_key = {(row["detector_id"], float(row["begin"]), float(row["end"])): row for row in expected}
fields, station_groups = [], defaultdict(list)
for row in comparison:
    metadata = source_by_key[row["detector_id"], float(row["begin"]), float(row["end"])]
    fields.append({"node_id": metadata["node_id"], "asset_id": metadata["asset_id"], **row})
    members = [(station, node, assets) for station, (node, assets) in STATIONS.items()
               if metadata["node_id"] == node and metadata["asset_id"] in assets]
    assert len(members) == 1
    station_groups[members[0][0], row["begin"], row["end"]].append(row)
assert len(station_groups) == 40
stations = []
for (station, begin, end), group in sorted(station_groups.items()):
    assert len(group) == len(STATIONS[station][1])
    stations.append({"station_stream_id": station, "begin": begin, "end": end,
                     "expected_total": sum(row["expected_total"] for row in group),
                     "measured_nVehContrib": sum(row["measured_nVehContrib"] for row in group),
                     "residual": sum(row["diff_nVehContrib_minus_expected"] for row in group),
                     "independent_evidence": False})
write_csv(OUT / "all-130-field-comparison.csv", fields, list(fields[0]))
write_csv(OUT / "all-40-station-comparison.csv", stations, list(stations[0]))

run = json.loads((NATIVE / "run.json").read_bytes())
monitor = json.loads((NATIVE / "stall-monitor.json").read_bytes())
final = ET.parse(NATIVE / "summary.xml").getroot()[-1]
completion = {key: int(final.get(key, "0")) for key in
              ("loaded", "inserted", "arrived", "running", "waiting", "teleports", "collisions", "discarded")}
assert completion == run["completion"]
vehicles = ET.parse(CACHED / "demand.rou.xml").getroot().findall("vehicle")
trips = ET.parse(NATIVE / "tripinfo.xml").getroot().findall("tripinfo")
healthy = (all(completion[k] == len(vehicles) for k in ("loaded", "inserted", "arrived"))
           and not any(completion[k] for k in ("running", "waiting", "teleports", "collisions", "discarded"))
           and len(trips) == len(vehicles)
           and {row.get("id") for row in trips} == {row.get("id") for row in vehicles}
           and all(not row.get("vaporized", "") for row in trips))
assert healthy == run["healthy"]
assert run["native_status"] == "pass" and run["observer_complete"] and monitor["status"] == "completed"
assert monitor["simulation_time_s"] == 14400
residuals = [row["diff_nVehContrib_minus_expected"] for row in fields]
station_residuals = [row["residual"] for row in stations]
metrics = {"loss": sum(r * r for r in residuals), "MAE": sum(map(abs, residuals)) / 130,
           "max_abs_error": max(map(abs, residuals)), "healthy": healthy,
           "station_MAE": sum(map(abs, station_residuals)) / 40,
           "station_max_abs_error": max(map(abs, station_residuals)), "completion": completion,
           "exact_count_fit": healthy and not any(residuals),
           "count_source": "Native E1 nVehContrib only", "field_rows": 130, "derived_station_rows": 40,
           "field_total_absolute_error": sum(map(abs, residuals)), "exact_field_rows": residuals.count(0),
           "station_total_absolute_error": sum(map(abs, station_residuals)),
           "exact_station_rows": station_residuals.count(0),
           "target_field_total": sum(row["expected_total"] for row in fields),
           "measured_field_total": sum(row["measured_nVehContrib"] for row in fields),
           "native_status": run["native_status"], "observer_complete": run["observer_complete"],
           "native_collision_events": len(ET.parse(NATIVE / "collisions.xml").getroot().findall("collision")),
           "formal_result": False}
(OUT / "metrics.json").write_text(json.dumps(metrics, indent=2) + "\n", encoding="utf-8")
assert all(file_sha256(Path(path)) == digest for path, digest in before.items())
provenance = {"source_dir": str(CACHED), "prepared_dir": str(PREPARED),
              "analysis": str(Path(__file__).resolve()), "count_source": "Native E1 nVehContrib only",
              "all_130_fields_retained": True, "all_40_derived_station_rows_retained": True,
              "input_files_unchanged": True, "source_sha256": before,
              "output_sha256": {p.name: file_sha256(p) for p in
                                (OUT / "analyze.py", OUT / "metrics.json", OUT / "all-130-field-comparison.csv",
                                 OUT / "all-40-station-comparison.csv")},
              "claim_boundary": "Completed native observations from an unhealthy run. These diagnostic losses are not accepted calibration. Station values sum the same field observations."}
(OUT / "provenance.json").write_text(json.dumps(provenance, indent=2) + "\n", encoding="utf-8")
print(json.dumps(metrics, indent=2))
