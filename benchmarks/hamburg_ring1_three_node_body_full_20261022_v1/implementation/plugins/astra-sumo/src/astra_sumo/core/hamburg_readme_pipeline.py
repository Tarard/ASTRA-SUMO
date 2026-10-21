"""Complete the README count workflow with declared lane waypoints and native E1 measurements."""

import gzip
from copy import deepcopy
import json
from pathlib import Path
import xml.etree.ElementTree as ET

import numpy as np

from .artifact_io import write_json_atomic
from .candidate_contracts import file_sha256
from .detector_demand import read_csv_rows, write_csv
from .detector_demand_replay import run_point_detector_demand
from .route_sensor_matrix import solve_nonnegative_integer_route_flows


def localize_hamburg_lane_waypoints(*, prepared_dir, output_dir):
    """Keep lane intentions only upstream of the assigned observation section."""
    source, output = Path(prepared_dir).resolve(strict=True), Path(output_dir).resolve()
    if output.exists() or source.is_relative_to(output):
        raise ValueError("Use a new preparation directory separate from its source.")

    def content(name):
        path = source / name
        return path.read_bytes() if path.is_file() else gzip.decompress((source / (name + ".gz")).read_bytes())

    names = ("network.net.xml", "demand.rou.xml", "field-loops.add.xml", "expected-counts.csv", "control.add.xml", "lane-intentions.json", "scenario.json")
    inputs = {name: content(name) for name in names}
    root = ET.fromstring(inputs["network.net.xml"])
    lanes = {lane.get("id"): lane for edge in root.findall("edge") for lane in edge.findall("lane")}
    lane_counts = {lane.get("id"): len(edge.findall("lane")) for edge in root.findall("edge") for lane in edge.findall("lane")}
    loops = {loop.get("id"): loop for loop in ET.fromstring(inputs["field-loops.add.xml"])}
    intentions = json.loads(inputs["lane-intentions.json"])
    assigned = {row["vehicle_id"]: row["field_id"] for row in intentions["vehicles"]}
    demand = ET.fromstring(inputs["demand.rou.xml"])
    vehicles = demand.findall("vehicle")
    if set(assigned) != {v.get("id") for v in vehicles}:
        raise ValueError("Supply exactly one observation intention for every vehicle.")
    for kind in demand.findall("vType"):
        kind.attrib.pop("lcKeepRight", None)
        kind.attrib.pop("lcSpeedGain", None)
    removed, added = 0, 0
    for vehicle in vehicles:
        for stop in vehicle.findall("stop"):
            vehicle.remove(stop)
            removed += 1
        loop = loops[assigned[vehicle.get("id")]]
        lane = lanes[loop.get("lane")]
        length = float(lane.get("length"))
        # Short single-lane sections need no lane choice; retain their native driving.
        if length <= 7.5:
            if lane_counts[lane.get("id")] > 1:
                raise ValueError("A short observed section needs an explicit lane-choice treatment.")
            continue
        ET.SubElement(vehicle, "stop", lane=lane.get("id"), endPos=f"{length - 7.5:.6f}", speed=lane.get("speed"))
        added += 1
    output.mkdir(parents=True)
    for name, data in inputs.items():
        (output / name).write_bytes(data)
    ET.ElementTree(demand).write(output / "demand.rou.xml", encoding="utf-8", xml_declaration=True)
    intentions["mechanism"] = "One moving lane waypoint 7.5 m before the assigned detector road ends; native lane choice elsewhere. Short single-lane sections need no waypoint."
    intentions["claim_boundary"] = "Declared lane intention. Real driver behaviour and native count fit remain unverified."
    write_json_atomic(output / "lane-intentions.json", intentions)
    scenario = json.loads(inputs["scenario.json"])
    scenario["lane_intention_assumptions"] = intentions["mechanism"]
    scenario["demand_sha256"] = file_sha256(output / "demand.rou.xml")
    write_json_atomic(output / "scenario.json", scenario)
    report = {"status": "prepared", "formal_result": False, "vehicles": len(vehicles),
        "removed_waypoints": removed, "added_waypoints": added,
        "source_directory": str(source), "input_sha256": {name: file_sha256(output / name) for name in names},
        "unchanged": [name for name in names if name not in {"demand.rou.xml", "lane-intentions.json", "scenario.json"}]}
    write_json_atomic(output / "local-lane-treatment.json", report)
    return report


def prepare_hamburg_readme_run(*, repository, output_dir):
    repository, output = Path(repository).resolve(strict=True), Path(output_dir).resolve()
    if output.exists():
        raise ValueError("Use a new end-to-end preparation directory.")
    old = repository / "benchmarks/hamburg_ring1_m1_2_demand_pool_20261021_v1/base"
    control_package = repository / "benchmarks/hamburg_ring1_m2_control_20261021_v1"
    source = repository / "benchmarks/hamburg_ring1_m1_demand_pool_20261021_v1/inputs"

    def read(file):
        return json.loads(gzip.decompress(file.read_bytes()) if file.suffix == ".gz" else file.read_bytes())

    profiles = read(old / "ideal-lane-profiles.json.gz")["profiles"]
    system = np.load(old / "provisional-system.npz")
    column_data = read(old / "columns.json.gz")
    root = ET.parse(source / "network.net.xml").getroot()
    native = read(old / "native-lanes.json.gz")["lanes"]
    lane_by_key = {(lane["edge_id"], lane["index"]): lane for lane in native.values()}
    connections = {
        (c.get("from"), int(c.get("fromLane")), c.get("to"), int(c.get("toLane"))): c
        for c in root.findall("connection")
    }
    customers = {
        e.get("id")
        for e in root.findall("edge")
        if e.find("param[@key='access']") is not None and e.find("param[@key='access']").get("value") == "customers"
    }
    bindings = read(control_package / "link-bindings.json.gz")["rows"]
    unsupported = {tuple(row["physical_key"]) for row in bindings if row["tls"].startswith("LSA") and not row["groups"]}
    observed_lanes = {row.get("lane") for row in ET.parse(source / "field-loops.add.xml").getroot()}
    guided_edges = {lane["edge_id"] for lane in native.values() if lane["id"] in observed_lanes}
    guided_edges.update(edge.get("id") for edge in root.findall("edge") if edge.get("to", "").startswith("LSA"))
    eligible = set()
    for index, p in enumerate(profiles):
        pairs = [
            (a, p["exit_lanes"][i], b, p["entry_lanes"][i + 1])
            for i, (a, b) in enumerate(zip(p["edges"], p["edges"][1:]))
        ]
        if (
            p["field_mask"].bit_count() == 1
            and not customers.intersection(p["edges"])
            and all(key not in unsupported and connections[key].get("dir") not in {"t", "T"} for key in pairs)
        ):
            eligible.add(index)
    columns, indices = [], []
    for index, col in enumerate(column_data["columns"]):
        if col["profile_index"] not in eligible:
            continue
        p = profiles[col["profile_index"]]
        row = col["field_rows"][0]
        field, period = row % 13, row // 13
        offset = p["field_offsets_s"][str(field)]
        lower = max(col["lower_s"], 3600 + period * 900 + 100 - offset)
        upper = min(col["upper_s"], 3600 + period * 900 + 650 - offset)
        if lower < upper:
            columns.append({**col, "lower_s": lower, "upper_s": upper})
            indices.append(index)
    H, B, y, cap = (
        system["response"][:, indices],
        system["entry_matrix"][:, indices],
        system["observations"],
        system["entry_capacities"],
    )
    assert H.shape[0] == 130 and np.all(H.sum(axis=1) > 0)
    solution = solve_nonnegative_integer_route_flows(
        H,
        y,
        route_costs=np.array([c["distance_km"] for c in columns]),
        entry_matrix=B,
        entry_capacities=cap,
        time_limit_seconds=60,
    )
    assert solution["status"] == "pass", solution
    x = np.array(solution["solution"])
    assert np.array_equal(H @ x, y) and np.all(B @ x <= cap) and x.sum() == y.sum() == 6687
    output.mkdir(parents=True)
    routes = ET.Element("routes")
    ET.SubElement(
        routes,
        "vType",
        id="readme_lane_intent",
        vClass="passenger",
        length="5",
        minGap="2.5",
        sigma="0",
        speedFactor="1",
        speedDev="0",
        lcKeepRight="0",
        lcSpeedGain="0",
    )
    vehicles, intentions = [], []
    for column, count in zip(columns, x):
        p = profiles[column["profile_index"]]
        for n in range(int(count)):
            depart = column["lower_s"] + (n + 0.5) * (column["upper_s"] - column["lower_s"]) / int(count)
            identity = f"readme_f{column['field_rows'][0] % 13}_b{column['field_rows'][0] // 13}_p{column['profile_index']}_v{n}"
            vehicle = ET.Element(
                "vehicle",
                id=identity,
                type="readme_lane_intent",
                depart=f"{depart:.9f}",
                departLane="best",
                departPos="5.1",
                departSpeed="max",
            )
            ET.SubElement(vehicle, "route", edges=" ".join(p["edges"]))
            for i, edge in enumerate(p["edges"]):
                if edge not in guided_edges:
                    continue
                lane = lane_by_key[edge, p["exit_lanes"][i]]
                # Moving waypoints retain SUMO's lane-change safety and add no scheduled dwell.
                if lane["length"] >= 7.5:
                    middle = min(15.0, lane["length"] / 2)
                    if middle > 5.1 and middle < lane["length"] - 0.02:
                        ET.SubElement(
                            vehicle, "stop", lane=lane["id"], endPos=f"{middle:.6f}", speed=str(lane["speed"])
                        )
                    ET.SubElement(
                        vehicle,
                        "stop",
                        lane=lane["id"],
                        endPos=f"{lane['length'] - 0.01:.6f}",
                        speed=str(lane["speed"]),
                    )
            vehicles.append(vehicle)
            intentions.append(
                {
                    "vehicle_id": identity,
                    "profile_index": column["profile_index"],
                    "field_row": column["field_rows"][0],
                    "field_id": None,
                    "depart_s": depart,
                    "free_flow_field_offset_s": next(iter(p["field_offsets_s"].values())),
                }
            )
    routes.extend(sorted(vehicles, key=lambda v: (float(v.get("depart")), v.get("id"))))
    ET.ElementTree(routes).write(output / "demand.rou.xml", encoding="utf-8", xml_declaration=True)
    loops = ET.parse(source / "field-loops.add.xml").getroot()
    field_ids = [r.get("id") for r in loops.findall("inductionLoop")]
    # The matrix uses canonical detector order, not additional-file order.
    expected = read_csv_rows(old / "provisional-field-comparison.csv")
    field_order = [r["detector_id"] for r in expected[:13]]
    assert set(field_order) == set(field_ids)
    for row in intentions:
        row["field_id"] = field_order[row["field_row"] % 13]
    write_json_atomic(
        output / "lane-intentions.json",
        {
            "mechanism": "SUMO moving lane waypoints on observed roads and official approaches; best departure lane; lcKeepRight=0, lcSpeedGain=0",
            "vehicles": intentions,
            "claim_boundary": "Declared driver lane intentions. Not measured natural lane choice or true OD.",
        },
    )
    write_json_atomic(output / "integer-proposal.json", solution)
    np.savez_compressed(
        output / "system.npz", response=H, observations=y, entry_matrix=B, entry_capacities=cap, solution=x
    )
    write_json_atomic(output / "columns.json", {"columns": columns, "entry_rows": column_data["entry_rows"]})
    wanted = [
        {
            key: value
            for key, value in r.items()
            if key not in {"provisional_passages", "provisional_residual", "native_E1_measured"}
        }
        for r in expected
    ]
    write_csv(output / "expected-counts.csv", wanted, list(wanted[0]))
    (output / "network.net.xml").write_bytes((source / "network.net.xml").read_bytes())
    (output / "field-loops.add.xml").write_bytes((source / "field-loops.add.xml").read_bytes())
    controls = ET.parse(control_package / "control-A.add.xml").getroot()
    for index, logic in enumerate(controls.findall("tlLogic")):
        if not logic.get("id").startswith("LSA") and logic.get("id") != "6390683126":
            replacement = deepcopy(root.find(f"tlLogic[@id='{logic.get('id')}']"))
            controls.remove(logic)
            controls.insert(index, replacement)
            logic = replacement
        logic.set("programID", "readme_fixed_A")
        blocked = {"LSA104_part0": 14, "LSA535_part0": 5}.get(logic.get("id"))
        if blocked is not None:
            for phase in logic.findall("phase"):
                state = list(phase.get("state"))
                state[blocked] = "r"
                phase.set("state", "".join(state))
    ET.ElementTree(controls).write(output / "control.add.xml", encoding="utf-8", xml_declaration=True)
    report = {
        "schema": "astra.hamburg-readme-end-to-end/v1",
        "status": "prepared",
        "formal_result": False,
        "exact_count_fit": False,
        "vehicles": len(vehicles),
        "vehicle_km": solution["objective_value"],
        "field_bins": 130,
        "station_bins": 40,
        "profiles_used": len({r["profile_index"] for r in intentions}),
        "all_integer_rows_exact": True,
        "network_sha256": file_sha256(output / "network.net.xml"),
        "control_sha256": file_sha256(output / "control.add.xml"),
        "demand_prior": "One observed field per vehicle; no background traffic, customer-access roads, turnarounds, repeated roads or repeated junctions. Retain all observation rows.",
        "time_prior": "Provisional rear-clear times spread within seconds 100–650 of each observed 900 s bin, with unchanged per-entry bin ceilings.",
        "control_assumptions": "Official M2 alternative A, Z15 declared K9; 58 other OSM controllers retain native actuated programs. Declared 104/535 wrong-lane movements held red. Unsupported paths remain in the network and are excluded from planned lane paths.",
        "unverified": "Native completion, lane realization and count fit remain to be measured.",
    }
    write_json_atomic(output / "scenario.json", report)
    return report


def run_hamburg_readme_prepared(*, prepared_dir, output_dir, sumo_binary, hold_observed_lanes=False, hold_junction_body=False):
    if hold_junction_body and not hold_observed_lanes:
        raise ValueError("Use the junction-body hold with the declared observation-lane hold.")
    p = Path(prepared_dir).resolve(strict=True)
    body_nodes = None
    if hold_junction_body:
        scenario = p / "scenario.json"
        if scenario.exists():
            body_nodes = json.loads(scenario.read_bytes()).get("junction_body_hold_nodes")
        elif scenario.with_name("scenario.json.gz").exists():
            body_nodes = json.loads(gzip.decompress(scenario.with_name("scenario.json.gz").read_bytes())).get("junction_body_hold_nodes")
    names = ("network.net.xml", "demand.rou.xml", "field-loops.add.xml", "expected-counts.csv", "control.add.xml")
    if hold_observed_lanes:
        names += ("lane-intentions.json",)
    if any(not (p / name).is_file() for name in names):
        staged = Path(output_dir).resolve().with_name(Path(output_dir).name + "-inputs")
        if staged.exists():
            raise ValueError("Use a new replay input directory.")
        staged.mkdir(parents=True)
        for name in names:
            file = p / name
            if file.is_file():
                content = file.read_bytes()
            else:
                content = gzip.decompress((p / (name + ".gz")).read_bytes())
            (staged / name).write_bytes(content)
        p = staged
    return run_point_detector_demand(
        net_file=p / "network.net.xml",
        route_file=p / "demand.rou.xml",
        e1_additional_file=p / "field-loops.add.xml",
        expected_counts_csv=p / "expected-counts.csv",
        output_dir=Path(output_dir),
        end_s=14400,
        sumo_binary=str(sumo_binary),
        step_length=0.1,
        seed=104,
        timeout_seconds=2400 if hold_observed_lanes else 1800,
        state_times_s=(14399.9,) if hold_observed_lanes else (),
        all_stopped_timeout_s=300,
        signal_program_file=p / "control.add.xml",
        monitor_interval_s=None if hold_observed_lanes else 1,
        lane_intentions_file=p / "lane-intentions.json" if hold_observed_lanes else None,
        junction_body_hold=hold_junction_body,
        junction_body_hold_nodes=tuple(body_nodes) if body_nodes is not None else None,
    )
