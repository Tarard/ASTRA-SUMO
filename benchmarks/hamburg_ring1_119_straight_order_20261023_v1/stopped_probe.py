"""Two-car native diagnostic: a recorded stopped left-turn body and approaching straight car."""

import argparse
import gzip
import json
from pathlib import Path
import subprocess
import xml.etree.ElementTree as ET

import sumo
import traci

parser = argparse.ArgumentParser()
parser.add_argument("--network", type=Path, required=True)
parser.add_argument("--output-dir", type=Path, required=True)
options = parser.parse_args()
out = options.output_dir.resolve()
out.mkdir(parents=True, exist_ok=False)
repo = Path(__file__).resolve().parents[2]
source = repo / "benchmarks/hamburg_ring1_od_spsa_five_rounds_20261023_v1/evaluations/iteration-0001-plus"
routes = ET.Element("routes")
for vtype in ET.fromstring(gzip.decompress((source / "demand.rou.xml.gz").read_bytes())).findall("vType"):
    routes.append(vtype)
ET.SubElement(routes, "route", id="left", edges="43866090#0 486864294")
ET.SubElement(routes, "route", id="straight", edges="199230909#0 486864294")
ET.SubElement(routes, "vehicle", id="victim", type="readme_lane_intent", route="left", depart="72", departLane="0", departSpeed="0")
ET.SubElement(routes, "vehicle", id="collider", type="readme_lane_intent", route="straight", depart="72", departLane="3", departSpeed="max")
ET.ElementTree(routes).write(out / "demand.rou.xml", encoding="utf-8", xml_declaration=True)
binary = Path(sumo.__file__).parent / "bin/sumo.exe"
(out / "signal-programs.add.xml").write_bytes(gzip.decompress((source / "signal-programs.add.xml.gz").read_bytes()))
command = [str(binary), "-n", str(options.network.resolve()), "-r", str(out / "demand.rou.xml"),
           "-a", str(out / "signal-programs.add.xml"), "--step-length", ".1", "--seed", "104", "--begin", "72",
           "--end", "130", "--collision.check-junctions", "true", "--no-step-log", "true", "--precision", "12",
           "--collision-output", str(out / "collisions.xml"), "--summary-output", str(out / "summary.xml"),
           "--fcd-output", str(out / "fcd.xml"), "--error-log", str(out / "sumo-errors.log")]
connection = None
try:
    traci.start(command, label=out.name, stdout=subprocess.DEVNULL)
    connection = traci.getConnection(out.name)
    connection.simulationStep()
    assert set(connection.vehicle.getIDList()) == {"victim", "collider"}
    connection.vehicle.moveTo("victim", ":LSA119_part0_0_0", 52.266416421554)
    connection.vehicle.moveTo("collider", ":LSA119_part0_10_1", 0)
    for identity in ("victim", "collider"):
        connection.vehicle.setLaneChangeMode(identity, 512)
    connection.vehicle.setSpeed("victim", 0)
    while connection.simulation.getTime() < 130:
        if connection.simulation.getTime() >= 100 and "victim" in connection.vehicle.getIDList():
            connection.vehicle.setSpeed("victim", -1)
        connection.simulationStep()
finally:
    if connection is not None:
        connection.close()
collisions = ET.parse(out / "collisions.xml").getroot()
last = ET.parse(out / "summary.xml").getroot().findall("step")[-1]
result = {"diagnostic_only": True, "command": command, "native_collisions": [c.attrib for c in collisions],
    "final_summary": last.attrib, "initialization": "Move the two vehicles to the recorded stopped left-turn position and the straight internal path entry; hold only the victim until t=100. No count calibration claim."}
(out / "result.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
print(json.dumps(result, indent=2))
