"""Count source-bound roads without proposing or applying any deletions."""

from collections import Counter
import csv
import gzip
import hashlib
from importlib.metadata import version
import json
from pathlib import Path
import xml.etree.ElementTree as ET

import sumolib


root = Path(__file__).resolve().parents[2]
source = root / "benchmarks/hamburg_ring1_primary_entry_full_20261022_v1/inputs"
output = Path(__file__).resolve().parent
net_path, demand_path = source / "network.net.xml", source / "demand.rou.xml.gz"
net = ET.parse(net_path).getroot()
roads = {edge.get("id"): edge for edge in net.findall("edge") if edge.get("function", "normal") == "normal"}
loaded = sumolib.net.readNet(str(net_path))
passenger = {edge.getID() for edge in loaded.getEdges() if any(lane.allows("passenger") for lane in edge.getLanes())}
route_root = ET.fromstring(gzip.decompress(demand_path.read_bytes()))
vehicles = route_root.findall("vehicle")
routes = [vehicle.find("route").get("edges").split() for vehicle in vehicles]
assert len(vehicles) == 8640 and all(len(route) == len(set(route)) for route in routes)
planned = Counter(edge for route in routes for edge in route)
entries = Counter(route[0] for route in routes)
assert set(planned) <= passenger <= set(roads)
lane_roads = {lane.get("id"): edge.get("id") for edge in roads.values() for lane in edge.findall("lane")}
fields = {}
for loop in ET.parse(source / "field-loops.add.xml").getroot():
    fields.setdefault(lane_roads[loop.get("lane")], []).append(loop.get("id"))
assert sum(map(len, fields.values())) == 13
rows = []
for identity, edge in sorted(roads.items()):
    rows.append({
        "road": identity, "source_type": edge.get("type", "unknown"),
        "allows_passenger": identity in passenger,
        "planned_vehicle_passages": planned[identity], "requested_entry_vehicles": entries[identity],
        "observed_fields": "|".join(fields.get(identity, [])),
        "touches_official_junction": any(edge.get(key, "").startswith(
            ("LSA104_", "LSA118_", "LSA119_", "LSA200_", "LSA535_")) for key in ("from", "to")),
        "deletion_decision": "not_assessed",
    })
with (output / "road-inventory.csv").open("w", encoding="utf-8", newline="") as handle:
    writer = csv.DictWriter(handle, fieldnames=list(rows[0]), lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
summary = {
    "reference_case_source_commit": (source / "source-commit.txt").read_text(encoding="utf-8").strip(),
    "sumolib_version": version("sumolib"),
    "inputs": [{"path": path.relative_to(root).as_posix(), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
               for path in (net_path, demand_path, source / "field-loops.add.xml")],
    "sumo_edge_functions": dict(Counter(edge.get("function", "normal") for edge in net.findall("edge"))),
    "normal_roads": len(roads), "passenger_roads": len(passenger), "planned_roads": len(planned),
    "normal_roads_without_planned_traffic": len(set(roads) - set(planned)),
    "passenger_roads_without_planned_traffic": len(passenger - set(planned)),
    "unused_normal_types": dict(sorted(Counter(roads[identity].get("type", "unknown")
                                              for identity in set(roads) - set(planned)).items())),
    "network_signal_programs": len(net.findall("tlLogic")),
    "network_changed": False, "new_simulations": 0,
    "claim_boundary": "Normal roads exclude internal paths, walking areas and crossings. Planned route usage is not measured field volume and does not justify deletion. Official-node adjacency is a geometry marker, not full official movement correspondence. Every deletion decision remains unassessed.",
}
assert summary["sumolib_version"] == "1.27.1"
(output / "scope-inventory.json").write_bytes((json.dumps(summary, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
print(json.dumps({key: summary[key] for key in ("normal_roads", "passenger_roads", "planned_roads",
    "passenger_roads_without_planned_traffic", "network_changed", "new_simulations")}))
