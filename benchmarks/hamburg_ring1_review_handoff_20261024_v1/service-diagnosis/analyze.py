"""Read one completed fixed-history service episode. No native run."""
import csv
import gzip
import hashlib
import json
from pathlib import Path
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parent
SOURCE = ROOT.parent
BEGIN, END = 4425, 4470
TLS_IDS = ("6390683114", "244203524", "244203525", "LSA119_part0")
FIELDS = ("hh_0119_Z_12_31546", "hh_0119_Z_13_34018")
EDGES = ("199500674#0", "199500674#1", "199500674#2", "24483192", "43866090#0", "486864294")

def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def save(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf8")

def write_csv(path, rows):
    with path.open("w", encoding="utf8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)

def main():
    sources = [SOURCE / "independent.json", SOURCE / "demand-side-diagnosis.json", Path(__file__)]
    netfile = SOURCE / "reference/prepared/network.net.xml"
    loopsfile = SOURCE / "reference/prepared/field-loops.add.xml"
    targetfile = SOURCE / "reference/prepared/expected-counts.csv"
    sources += [netfile, loopsfile, targetfile]
    with targetfile.open(encoding="utf8", newline="") as handle:
        target_sum = sum(int(row["expected_total"]) for row in csv.DictReader(handle) if row["detector_id"] in FIELDS and float(row["begin"]) == 3600)
    assert target_sum == 264
    net = ET.parse(netfile).getroot()
    lanes = {lane.get("id"): lane for edge in net.findall("edge") for lane in edge.findall("lane")}
    loops = [dict(loop.attrib, lane_length_m=lanes[loop.get("lane")].get("length"))
             for loop in ET.parse(loopsfile).getroot() if loop.get("id") in FIELDS]
    assert len(loops) == 2 and all(float(row["pos"]) == 13.4 and float(row["lane_length_m"]) == 13.5 for row in loops)
    movements = [dict(c.attrib) for c in net.findall("connection") if c.get("from") in EDGES]
    assert {(c["fromLane"], c["toLane"], c.get("linkIndex")) for c in movements if c["from"] == "43866090#0"} == {("0", "0", "0"), ("1", "1", "1")}
    queue_rows, state_rows, results = [], [], {}
    for variant in ("reference", "warmup9"):
        native = SOURCE / variant / "evaluation/native"
        compact_path = native.parent / "contributing-passages.json.gz"
        sources += [compact_path] + [native / f for f in ("vehicle-routes.xml", "queues.xml", "tls-switch-states.xml")]
        compact = json.loads(gzip.decompress(compact_path.read_bytes()))
        assert compact["reconciled"] and compact["inputs_unchanged"]
        passages = {p["vehicle_id"]: p for p in compact["passages"]}
        vehicles = {}
        for _, element in ET.iterparse(native / "vehicle-routes.xml", events=("end",)):
            if element.tag != "vehicle":
                continue
            route = element.find("route")
            edges = route.get("edges").split()
            times = dict(zip(edges, map(float, route.get("exitTimes").split()), strict=True))
            if "43866090#0" in times:
                identity = element.get("id")
                assert passages[identity]["detector_id"] in FIELDS
                assert "24483192" in times and "486864294" in times
                vehicles[identity] = times
            element.clear()
        last_state = {}
        for _, element in ET.iterparse(native / "tls-switch-states.xml", events=("end",)):
            if element.tag == "tlsState" and element.get("id") in TLS_IDS:
                t = float(element.get("time"))
                if t <= BEGIN:
                    last_state[element.get("id")] = dict(element.attrib)
                elif BEGIN < t <= END:
                    state_rows.append(dict(variant=variant, **element.attrib))
            element.clear()
        state_rows.extend(dict(variant=variant, **row) for row in last_state.values())
        for _, element in ET.iterparse(native / "queues.xml", events=("end",)):
            if element.tag != "data":
                continue
            t = float(element.get("timestep"))
            if BEGIN <= t <= END:
                present = {row.get("id"): row for row in element.findall("./lanes/lane")}
                for lane in lanes:
                    if any(lane.startswith(edge + "_") for edge in EDGES):
                        row = present.get(lane)
                        queue_rows.append(dict(variant=variant, native_time_s=t, lane=lane, listed=row is not None,
                            queueing_length_m=row.get("queueing_length") if row is not None else None,
                            queueing_length_experimental_m=row.get("queueing_length_experimental") if row is not None else None))
            element.clear()
            if t > END:
                break
        fields = {}
        for field in FIELDS:
            members = {v: times for v, times in vehicles.items() if passages[v]["detector_id"] == field}
            exits = [(times["43866090#0"], v) for v, times in members.items() if 4431 <= times["43866090#0"] < 4460]
            rear = [(passages[v]["notification_time_s"], v) for v in members if 4431 <= passages[v]["notification_time_s"] < 4460]
            last_exit, last_id = max(exits)
            last_rear, _ = max(rear)
            # This interval covers the263002865 internal connection plus438 ordinary edge.
            post_last_arrivals = [v for v, times in members.items() if last_exit < times["24483192"] < 4460]
            assert not post_last_arrivals
            assert not [v for v, times in members.items() if times["24483192"] <= last_exit < times["43866090#0"]]
            fields[field] = dict(ordinary_438_exit_count=len(exits), E1_rear_count=len(rear),
                last_438_ordinary_exit_s=last_exit, last_exit_vehicle=last_id, last_E1_rear_notification_s=last_rear,
                no_new_244_exit_after_last_438_exit_until_s=4460,
                last_vehicle_edge_exit_times={edge: members[last_id].get(edge) for edge in EDGES})
        near = []
        for identity, times in vehicles.items():
            if "199500674#2" in times and times["199500674#1"] <= 4455 < times["199500674#2"]:
                near.append(dict(vehicle_id=identity, field=passages[identity]["detector_id"],
                    edge_exit_times={edge: times[edge] for edge in EDGES}, E1_rear_notification_s=passages[identity]["notification_time_s"]))
        assert near
        results[variant] = dict(actual_first_window_Z12_Z13=sum(3600 <= passages[v]["notification_time_s"] < 4500 for v in vehicles),
            fields=fields, vehicles_between_199_1_exit_and_199_2_exit_at4455=near)
    assert all(row["actual_first_window_Z12_Z13"] == 255 for row in results.values())
    write_csv(ROOT / "queues.csv", queue_rows)
    write_csv(ROOT / "actual-tls-events.csv", sorted(state_rows, key=lambda row: (row["variant"], float(row["time"]), row["id"])))
    audit = json.loads((SOURCE / "independent.json").read_bytes())
    assert audit["reference14_strict_parity_verified"] and audit["all50_actual_colors_programs_difference_ticks_zero"]
    save(ROOT / "summary.json", dict(schema="astra.hamburg.fixed-primary-service-episode/v1", status="pass", new_native_runs=0,
        window_s=[BEGIN, END], target_green_s=[4431, 4460], true_first_window_target_Z12_Z13=target_sum,
        detectors=loops, actual_controlled_movements=movements, cases=results,
        finding="Both cases exhaust the measured244-exit-to438-exit front-boundary stock before the119 green ends. Native routes locate subsequent119-bound vehicles upstream at199#2 during its through red, then arriving beyond the119 green. This supports an upstream service-timing restriction in this episode. It does not prove every missing contribution has this cause.",
        boundaries=["E1 is0.1m before the ordinary edge end. Complete rear contributions and ordinary route exitTimes are separate observables.",
            "An exit-to-exit interval includes the connecting internal path. It is not stopped time or an exact lane position.",
            "Queue output has no vehicle IDs. An unlisted lane is unknown, not proven empty. Recorded queues cannot locate an individual vehicle.",
            "No per-vehicle FCD or lane-change record exists here. Which upstream lane/stopline first constrains reception still needs a local passive observation.",
            "255 is an observed first-window total, not a hard capacity limit. Simulated frozen colors are not fieldSPaT."],
        next_observation=dict(source="reference", fresh_prefix_end_s=4500, local_window_s=[4425,4470],
            ordinary_edges=list(EDGES), tls_ids=list(TLS_IDS), purpose="Locate heads and their actual controlling links in the three short upstream signalized edges while119 remains green. Preserve all driving inputs."),
        source_hashes={str(path.resolve()): sha(path) for path in sources}))
    save(ROOT / "manifest.json", dict(status="pass", native_runs=0, files=[dict(path=p.name, sha256=sha(p), bytes=p.stat().st_size) for p in sorted(ROOT.iterdir()) if p.is_file() and p.name != "manifest.json"]))
    print(json.dumps(dict(status="pass", cases=results, manifest_sha256=sha(ROOT / "manifest.json"))))

if __name__ == "__main__":
    main()
