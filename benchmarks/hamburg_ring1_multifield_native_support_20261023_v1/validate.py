"""Offline checks for the two frozen multi-field witnesses."""

import argparse
from collections import Counter, defaultdict
import gzip
import hashlib
import json
import math
from pathlib import Path
import xml.etree.ElementTree as ET


def raw(path):
    return path.read_bytes() if path.is_file() else gzip.decompress(path.with_name(path.name + ".gz").read_bytes())


def complete_events(instant, mapping, counts, expected):
    events = []
    for row in instant:
        if row.get("state") != "leave":
            continue
        assert float(row.get("occupancy")) > 0
        field, vehicle, time = mapping[row.get("id")], row.get("vehID"), float(row.get("time"))
        assert vehicle in expected
        notified = math.ceil(math.nextafter(time/.1, -math.inf))*.1
        interval = next(k for k in counts if k[0] == field and k[1] <= notified < k[2])
        events.append({"detector_id": field, "vehicle_id": vehicle, "time_s": time,
                       "notification_time_s": notified, "occupancy_s": float(row.get("occupancy")),
                       "interval_begin_s": interval[1], "interval_end_s": interval[2]})
    assert all(n == 1 for n in Counter((r["vehicle_id"], r["detector_id"]) for r in events).values())
    assert Counter((r["detector_id"], r["interval_begin_s"], r["interval_end_s"]) for r in events) == Counter(counts)
    signatures = defaultdict(list)
    for row in sorted(events, key=lambda x: x["time_s"]):
        signatures[row["vehicle_id"]].append(row["detector_id"])
    assert dict(signatures) == expected
    return events


def verify(case):
    manifest = json.loads((case / "manifest.json").read_bytes())
    actual = {p.relative_to(case).as_posix() for p in case.rglob("*")
              if p.is_file() and p != case / "manifest.json" and "__pycache__" not in p.parts}
    assert actual == {r["path"] for r in manifest["files"]}
    for row in manifest["files"]:
        value = (case / row["path"]).read_bytes()
        assert len(value) == row["bytes"] and hashlib.sha256(value).hexdigest() == row["sha256"]
    for row in json.loads((case / "source-provenance.json").read_bytes())["snapshots"]:
        value = gzip.decompress((case / row["snapshot"]).read_bytes())
        assert len(value) == row["raw_bytes"] and hashlib.sha256(value).hexdigest() == row["raw_sha256"]
    result = json.loads(raw(case / "result.json"))
    run = json.loads(raw(case / "native/run.json"))
    demand = ET.fromstring(raw(case / "demand.rou.xml")).findall("vehicle")
    actual_routes = {v.get("id"): v for v in ET.fromstring(raw(case / "native/vehicle-routes.xml")).findall("vehicle")}
    assert len(demand) == len(actual_routes) == 2
    for vehicle in demand:
        route = vehicle.find("route").get("edges").split()
        assert len(route) == len(set(route))
        assert actual_routes[vehicle.get("id")].find("route").get("edges").split() == route
        assert [r.get("lane") for r in vehicle.findall("stop")] == ["43866090#0_0", "186821034#0_5"]
        assert [r.get("endPos") for r in vehicle.findall("stop")] == ["6.000000", "15.120000"]
        assert all(r.get("speed") == "13.888889" for r in vehicle.findall("stop"))
    counts = {}
    for row in ET.fromstring(raw(case / "native/field-counts.xml")).findall("interval"):
        key = row.get("id"), float(row.get("begin")), float(row.get("end"))
        if key[1] != key[2]:
            assert key not in counts
            counts[key] = int(row.get("nVehContrib"))
    assert len(counts) == 208 and sum(counts.values()) == 4
    events = complete_events(ET.fromstring(raw(case / "native/instant-e1.xml")), run["instant_to_periodic_ids"], counts, result["expected_signatures"])
    assert events == result["passages"]["passages"] and result["status"] == "pass"
    final = ET.fromstring(raw(case / "native/summary.xml"))[-1]
    completion = {k: int(final.get(k)) for k in result["completion"]}
    assert completion == result["completion"] == run["completion"]
    assert all(completion[k] == 2 for k in ("loaded", "inserted", "arrived"))
    assert not any(completion[k] for k in ("running", "waiting", "collisions", "teleports", "discarded"))
    assert not ET.fromstring(raw(case / "native/collisions.xml")).findall("collision")
    trips = ET.fromstring(raw(case / "native/tripinfo.xml")).findall("tripinfo")
    assert len(trips) == 2 and {t.get("id") for t in trips} == actual_routes.keys()
    assert all(float(t.get("arrival")) >= 0 and not t.get("vaporized") for t in trips)
    assert result["healthy"] and not result["formal_count_calibration"]
    print("Frozen bytes, two routes/stops, four complete native events, identities and completion: pass")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", type=Path, default=Path(__file__).resolve().parent)
    verify(parser.parse_args().case.resolve(strict=True))
