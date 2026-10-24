"""Screen the frozen Hamburg demand against declared nominal signal capacities."""

from collections import defaultdict
from fractions import Fraction
import math
from pathlib import Path
import xml.etree.ElementTree as ET

import numpy as np
from scipy.optimize import linprog

from .artifact_io import write_json_atomic
from .candidate_contracts import file_sha256
from .detector_demand import read_csv_rows, write_csv
from .hamburg_control_inventory import _json
from .route_sensor_matrix import solve_nonnegative_integer_route_flows


def nominal_capacity(green_s, cycle_s, factor):
    return math.floor(Fraction(1800, 4) * Fraction(green_s) / Fraction(cycle_s) * Fraction(factor))


def rational_service_upper_bound(matrix, capacities, dual):
    """Certify an upper bound on total service when every column serves one field."""
    q = [Fraction(math.ceil(max(0.0, -float(value)) * 10**6), 10**6) for value in dual]
    supports = [np.flatnonzero(matrix[:, column]) for column in range(matrix.shape[1])]
    coverage = [sum(q[row] * int(matrix[row, column]) for row in support) for column, support in enumerate(supports)]
    if not coverage or min(coverage) <= 0:
        raise ValueError("The rounded dual does not cover every positive-service column.")
    scale = max(Fraction(1), 1 / min(coverage))
    q = [value * scale for value in q]
    assert all(
        sum(q[row] * int(matrix[row, column]) for row in support) >= 1 for column, support in enumerate(supports)
    )
    bound = sum(int(cap) * value for cap, value in zip(capacities, q))
    return {
        "rational_bound": [bound.numerator, bound.denominator],
        "integer_service_upper_bound": math.floor(bound),
        "nonzero_dual": [
            {"row_index": i, "coefficient": [value.numerator, value.denominator]} for i, value in enumerate(q) if value
        ],
        "all_column_inequalities_verified": True,
    }


def screen_hamburg_capacity(*, case_dir, output_dir):
    case, out = Path(case_dir).resolve(strict=True), Path(output_dir).resolve()
    if out.exists():
        raise ValueError("Use a new signal-capacity screen directory.")
    inputs = case / "inputs"
    system = np.load(inputs / "system.npz")
    original_columns = _json(inputs / "columns.json")["columns"]
    entry_rows = _json(inputs / "columns.json")["entry_rows"]
    profiles_file = case.parent / "hamburg_ring1_m1_2_demand_pool_20261021_v1/base/ideal-lane-profiles.json.gz"
    profiles = _json(profiles_file)["profiles"]
    net, control = ET.parse(inputs / "network.net.xml").getroot(), ET.parse(inputs / "control.add.xml").getroot()
    connections = {
        (c.get("from"), int(c.get("fromLane")), c.get("to"), int(c.get("toLane"))): c
        for c in net.findall("connection")
        if c.get("tl")
    }
    programs = {logic.get("id"): logic for logic in control.findall("tlLogic")}
    profile_events, planned_keys = {}, set()
    for index in {c["profile_index"] for c in original_columns}:
        profile = profiles[index]
        events = []
        for i, (a, b) in enumerate(zip(profile["edges"], profile["edges"][1:])):
            key = (a, profile["exit_lanes"][i], b, profile["entry_lanes"][i + 1])
            if key in connections:
                events.append((key, profile["approach_offsets_s"][i]))
                planned_keys.add(key)
        profile_events[index] = events
    lane_members = defaultdict(set)
    resources = {}
    for key in sorted(planned_keys):
        c = connections[key]
        tls = c.get("tl")
        logic = programs[tls]
        phases = logic.findall("phase")
        index = int(c.get("linkIndex"))
        cycle = sum((Fraction(p.get("duration")) for p in phases), Fraction(0))
        green = sum((Fraction(p.get("duration")) for p in phases if p.get("state")[index] in "GgOo"), Fraction(0))
        identity = ("movement", tls, *key)
        resources[identity] = {
            "kind": "signal_movement",
            "tls": tls,
            "physical_keys": [list(key)],
            "indices": [index],
            "green_s": str(green),
            "cycle_s": str(cycle),
            "program_type": logic.get("type"),
            "time_basis": "nominal program durations, not realized actuated greens",
        }
        lane_members[tls, key[0], key[1]].add(key)
    for (tls, edge, lane), members in lane_members.items():
        logic = programs[tls]
        phases = logic.findall("phase")
        indices = {int(connections[key].get("linkIndex")) for key in members}
        green = sum(
            (Fraction(p.get("duration")) for p in phases if any(p.get("state")[i] in "GgOo" for i in indices)),
            Fraction(0),
        )
        cycle = sum((Fraction(p.get("duration")) for p in phases), Fraction(0))
        resources["lane", tls, edge, lane] = {
            "kind": "shared_signal_inlet_lane",
            "tls": tls,
            "physical_keys": [list(k) for k in sorted(members)],
            "indices": sorted(indices),
            "green_s": str(green),
            "cycle_s": str(cycle),
            "program_type": logic.get("type"),
            "time_basis": "nominal union of permitted intervals for planned movements on one motor lane",
        }
    columns = []
    for source_index, column in enumerate(original_columns):
        events = profile_events[column["profile_index"]]
        lower, upper = column["lower_s"], column["upper_s"]
        boundaries = {lower, upper}
        for key, offset in events:
            for period in range(math.floor((lower + offset) / 900) + 1, math.floor((upper + offset) / 900) + 1):
                if lower < period * 900 - offset < upper:
                    boundaries.add(period * 900 - offset)
        ordered = sorted(boundaries)
        for a, b in zip(ordered, ordered[1:]):
            if b - a <= 0.2:
                continue
            middle = (a + b) / 2
            used = []
            for key, offset in events:
                tls = connections[key].get("tl")
                period = math.floor((middle + offset) / 900)
                used.extend([(("movement", tls, *key), period), (("lane", tls, key[0], key[1]), period)])
            columns.append(
                {
                    **column,
                    "lower_s": a + 0.1,
                    "upper_s": b - 0.1,
                    "source_column": source_index,
                    "signal_rows": list(dict.fromkeys(used)),
                }
            )
    signal_keys = sorted({key for column in columns for key in column["signal_rows"]})
    indices = {key: i for i, key in enumerate(signal_keys)}
    H = system["response"][:, [c["source_column"] for c in columns]]
    entry = system["entry_matrix"][:, [c["source_column"] for c in columns]]
    signals = np.zeros((len(signal_keys), len(columns)), dtype=np.int64)
    for j, column in enumerate(columns):
        for key in column["signal_rows"]:
            signals[indices[key], j] = 1
    assert H.shape[0] == 130 and np.all(H.sum(axis=0) == 1)
    y = system["observations"]
    matrix = np.vstack([entry, signals])
    metadata = [
        {"kind": "entry", "entry": edge, "begin_s": period * 900, "end_s": (period + 1) * 900}
        for edge, period in entry_rows
    ]
    metadata.extend(
        {**resources[key], "resource_key": list(key), "begin_s": period * 900, "end_s": (period + 1) * 900}
        for key, period in signal_keys
    )
    expected = read_csv_rows(inputs / "expected-counts.csv")
    assert np.array_equal(y, [int(r["expected_total"]) for r in expected])
    out.mkdir(parents=True)
    summaries = []
    for label, factor in [("main", Fraction(4, 5)), ("no_margin_sensitivity", Fraction(1))]:
        capacities = np.concatenate(
            [
                system["entry_capacities"],
                np.array(
                    [
                        nominal_capacity(resources[key]["green_s"], resources[key]["cycle_s"], factor)
                        for key, period in signal_keys
                    ],
                    dtype=np.int64,
                ),
            ]
        )
        integer = solve_nonnegative_integer_route_flows(
            H,
            y,
            route_costs=np.array([c["distance_km"] for c in columns]),
            entry_matrix=matrix,
            entry_capacities=capacities,
            time_limit_seconds=60,
        )
        write_json_atomic(out / f"{label}-integer.json", integer)
        augmented = np.vstack([matrix, H])
        bounds = np.concatenate([capacities, y])
        relaxed = linprog(
            -np.ones(len(columns)),
            A_ub=augmented,
            b_ub=bounds,
            bounds=(0, None),
            method="highs",
            options={"time_limit": 60},
        )
        certificate = (
            rational_service_upper_bound(augmented, bounds, relaxed.ineqlin.marginals) if relaxed.success else None
        )
        if certificate:
            for row in certificate["nonzero_dual"]:
                index = row["row_index"]
                row["constraint"] = (
                    metadata[index]
                    if index < len(metadata)
                    else {
                        "kind": "observed_field_upper_bound",
                        "field_row": index - len(metadata),
                        "detector_id": expected[index - len(metadata)]["detector_id"],
                    }
                )
                row["rhs"] = int(bounds[index])
        write_json_atomic(out / f"{label}-certificate.json", certificate)
        local = []
        for f, row in enumerate(expected):
            candidate = np.flatnonzero(H[f])
            mandatory = np.flatnonzero(np.all(matrix[:, candidate] > 0, axis=1))
            tight = min(mandatory, key=lambda i: capacities[i]) if len(mandatory) else None
            local.append(
                {
                    **row,
                    "necessary_capacity_upper_bound": int(capacities[tight]) if tight is not None else None,
                    "necessary_constraint_index": int(tight) if tight is not None else None,
                    "target_exceeds_necessary_capacity": tight is not None and y[f] > capacities[tight],
                    "simultaneous_LP_service": float((H @ relaxed.x)[f]) if relaxed.success else None,
                    "source": "nominal planning screen, not SUMO counts",
                }
            )
        write_csv(out / f"{label}-field-screen.csv", local, list(local[0]))
        summaries.append(
            {
                "case": label,
                "factor": str(factor),
                "integer_status": integer["status"],
                "failure_kind": integer.get("failure_kind"),
                "maximum_simultaneous_LP_service": float(-relaxed.fun) if relaxed.success else None,
                "certified_integer_service_upper_bound": certificate["integer_service_upper_bound"]
                if certificate
                else None,
                "target_passages": int(y.sum()),
                "individually_limited_field_bins": int(sum(r["target_exceeds_necessary_capacity"] for r in local)),
                "infeasibility_certified": certificate is not None
                and certificate["integer_service_upper_bound"] < int(y.sum()),
            }
        )
        np.savez_compressed(
            out / f"{label}-system.npz",
            response=H,
            observations=y,
            resource_matrix=matrix,
            resource_capacities=capacities,
        )
    write_json_atomic(out / "resources.json", {"rows": metadata})
    write_json_atomic(out / "columns.json", {"columns": columns})
    lanes = {lane.get("id"): lane for edge in net.findall("edge") for lane in edge.findall("lane")}
    proposal = []
    demand = ET.fromstring(__import__("gzip").decompress((inputs / "demand.rou.xml.gz").read_bytes()))
    counts = defaultdict(int)
    for vehicle in demand.findall("vehicle"):
        for stop in vehicle.findall("stop"):
            counts[stop.get("lane")] += 1
    for lane, count in counts.items():
        length = float(lanes[lane].get("length"))
        proposal.append(
            {
                "lane": lane,
                "vehicles_using_waypoint": count,
                "old_end_pos_m": length - 0.01,
                "proposed_end_pos_m": length - 7.5,
                "lane_length_m": length,
                "distance_before_lane_end_m": 7.5,
                "native_behavior_verified": False,
            }
        )
    write_csv(out / "waypoint-relocation-proposal.csv", proposal, list(proposal[0]))
    report = {
        "schema": "astra.hamburg-capacity-screen/v1",
        "status": "measured",
        "formal_result": False,
        "native_simulation_performed": False,
        "field_rows_retained": 130,
        "station_rows_derived_from_fields": 40,
        "columns": len(columns),
        "resource_rows": len(metadata),
        "cases": summaries,
        "saturation_flow_assumption_veh_per_h_per_lane": 1800,
        "main_margin_factor": "4/5",
        "actuated_limit": "Nominal duration shares only; these are not hard bounds on realized actuated throughput.",
        "time_limit": "Resource bins use free-flow offsets, not queued native passage times.",
        "claim_boundary": "A rational certificate concerns this finite planning model with added nominal capacity priors. It does not prove that the original SUMO model or field traffic cannot reach the targets.",
        "inputs": [
            {"path": str(file), "sha256": file_sha256(file)}
            for file in [inputs / "network.net.xml", inputs / "control.add.xml", inputs / "system.npz", profiles_file]
        ],
    }
    write_json_atomic(out / "report.json", report)
    return report
