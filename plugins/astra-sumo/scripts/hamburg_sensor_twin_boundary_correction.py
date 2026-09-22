from __future__ import annotations

import argparse
import xml.etree.ElementTree as ET
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--demand", required=True, type=Path)
    parser.add_argument("--vehroute", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--damping", type=float, default=0.5)
    parser.add_argument("--margin", type=float, default=60.0)
    args = parser.parse_args()
    if not 0 < args.damping <= 1 or args.margin < 0:
        raise ValueError("boundary correction parameters are invalid")

    tree = ET.parse(args.demand.resolve(strict=True))
    root = tree.getroot()
    demand = {vehicle.attrib["id"]: vehicle for vehicle in root.findall("vehicle")}
    changed = 0
    for vehicle in ET.parse(args.vehroute.resolve(strict=True)).getroot().findall("vehicle"):
        route = vehicle.find("route")
        if route is None:
            continue
        edges = tuple(route.attrib["edges"].split())
        approach = next((edge for edge in ("186821034#0", "186821035#0") if edge in edges), None)
        if approach is None:
            continue
        times = [float(value) for value in route.attrib["exitTimes"].split()]
        passage = times[edges.index(approach)]
        if passage < 1800:
            correction = 1800 + args.margin - passage
        elif passage >= 10800:
            correction = 10800 - args.margin - passage
        else:
            continue
        source = demand[vehicle.attrib["id"]]
        depart = max(0.0, float(source.attrib["depart"]) + args.damping * correction)
        source.attrib["depart"] = f"{depart:.2f}"
        source.attrib["departLane"] = "free"
        changed += 1
    ordered = sorted(
        root.findall("vehicle"),
        key=lambda vehicle: (float(vehicle.attrib["depart"]), vehicle.attrib["id"]),
    )
    for vehicle in list(root.findall("vehicle")):
        root.remove(vehicle)
    root.extend(ordered)
    ET.indent(tree, space="    ")
    tree.write(args.output, encoding="utf-8", xml_declaration=True)
    print(changed)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
