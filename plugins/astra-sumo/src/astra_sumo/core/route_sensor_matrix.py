from __future__ import annotations

import csv
import copy
import math
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from bisect import bisect_right
from pathlib import Path

import numpy as np
from scipy.optimize import Bounds, LinearConstraint, milp


def read_contributing_e1_passages(*, instant_file: Path, periodic_file: Path,
                                 detector_ids: dict[str, str], step_length: float) -> dict:
    """Reconcile full-pass event identities against the supplied periodic E1 counts.

    SUMO instantaneous leaves with occupancy describe completed passes.
    Leaves without occupancy are early departures and do not replace nVehContrib.
    """
    from .candidate_contracts import file_sha256
    from .detector_demand import e1_counts_by_detector_interval_strict, safe_id

    if not math.isfinite(step_length) or step_length <= 0:
        raise ValueError("The simulation step must be positive and finite.")
    instant_file, periodic_file = Path(instant_file).resolve(strict=True), Path(periodic_file).resolve(strict=True)
    inputs = [{"path": str(path), "sha256": file_sha256(path)} for path in (instant_file, periodic_file)]
    counts = e1_counts_by_detector_interval_strict(periodic_file, count_attribute="nVehContrib")
    mapping = {key: safe_id(value) for key, value in detector_ids.items()}
    if (not mapping or len(set(mapping.values())) != len(mapping)
            or set(mapping.values()) != {key[0] for key in counts}):
        raise ValueError("Instantaneous detectors must map one-to-one to all supplied periodic detectors.")
    intervals = defaultdict(list)
    for key in counts:
        intervals[key[0]].append((float(key[1]), float(key[2]), key))
    for rows in intervals.values():
        rows.sort()
        if any(first[1] > second[0] for first, second in zip(rows, rows[1:])):
            raise ValueError("Periodic detector intervals must not overlap.")
    starts = {field: [row[0] for row in rows] for field, rows in intervals.items()}
    root = ET.parse(instant_file).getroot()
    if root.tag != "instantE1":
        raise ValueError("The instantaneous output must have an instantE1 root.")
    observed, passages, early, outside = Counter(), [], [], []
    for row in root.findall("instantOut"):
        if row.get("state") != "leave":
            continue
        detector, vehicle = row.get("id"), row.get("vehID")
        time = float(row.get("time", "nan"))
        if detector not in mapping or not vehicle or not math.isfinite(time) or time < 0:
            raise ValueError("A leave event has an unknown detector, missing vehicle, or invalid time.")
        event = {"detector_id": mapping[detector], "vehicle_id": vehicle, "time_s": time}
        if row.get("occupancy") is None:
            early.append(event)
            continue
        occupancy = float(row.get("occupancy"))
        if not math.isfinite(occupancy) or occupancy <= 0:
            raise ValueError("A complete passage must have positive finite occupancy.")
        notification = math.ceil(math.nextafter(time / step_length, -math.inf)) * step_length
        event.update(notification_time_s=notification, occupancy_s=occupancy)
        field = mapping[detector]
        index = bisect_right(starts[field], notification) - 1
        if index < 0 or notification >= intervals[field][index][1]:
            outside.append(event)
            continue
        key = intervals[field][index][2]
        observed[key] += 1
        passages.append({**event, "interval_begin_s": float(key[1]), "interval_end_s": float(key[2])})
    comparisons = [{"detector_id": key[0], "begin_s": float(key[1]), "end_s": float(key[2]),
        "periodic_nVehContrib": count, "complete_passage_count": observed[key],
        "difference": observed[key] - count} for key, count in sorted(
            counts.items(), key=lambda item: (item[0][0], float(item[0][1]), float(item[0][2])))]
    return {"status": "measured", "reconciled": all(row["difference"] == 0 for row in comparisons),
        "measurement_attribute": "nVehContrib", "step_length_s": step_length, "inputs": inputs,
        "inputs_unchanged": all(file_sha256(Path(row["path"])) == row["sha256"] for row in inputs),
        "passages": passages, "early_departures": early, "outside_periodic_intervals": outside,
        "interval_comparisons": comparisons,
        "claim_boundary": "Event correspondence for the supplied periodic intervals only. No inferred field truth, demand fit, or invariance under changed traffic."}


def solve_nonnegative_integer_route_flows(
    response_matrix: np.ndarray,
    observations: np.ndarray,
    *,
    prior: np.ndarray | None = None,
    route_costs: np.ndarray | None = None,
    time_limit_seconds: float = 60.0,
) -> dict[str, object]:
    """Find one exact integer route-flow vector for a sensor response matrix.

    ``response_matrix[s, r]`` is the number of contributions made by one
    vehicle on route ``r`` to sensor group ``s``.  When a prior is supplied,
    the solver minimizes the L1 change from that prior; otherwise it minimizes
    the number of vehicles. Positive route costs can weight either objective.
    For example, route lengths give a vehicle-distance objective without a prior.
    The function deliberately returns ``blocked``
    rather than weakening an infeasible equality into an undocumented fit.
    """

    matrix_integer = _exact_nonnegative_integer_array(response_matrix, "response_matrix")
    counts_integer = _exact_nonnegative_integer_array(observations, "observations")
    if (
        matrix_integer.ndim != 2
        or matrix_integer.shape[0] == 0
        or matrix_integer.shape[1] == 0
    ):
        raise ValueError("response_matrix must be a non-empty two-dimensional matrix")
    if counts_integer.ndim != 1 or counts_integer.shape[0] != matrix_integer.shape[0]:
        raise ValueError("observations must have one value per sensor row")
    if not math.isfinite(time_limit_seconds) or time_limit_seconds <= 0:
        raise ValueError("time_limit_seconds must be finite and positive")
    matrix = matrix_integer.astype(np.float64)
    counts = counts_integer.astype(np.float64)

    route_count = matrix.shape[1]
    costs = np.ones(route_count, dtype=np.float64)
    if route_costs is not None:
        values = np.asarray(route_costs)
        if (values.ndim != 1 or values.shape[0] != route_count
                or not np.issubdtype(values.dtype, np.number) or np.issubdtype(values.dtype, np.complexfloating)
                or np.any(~np.isfinite(values)) or np.any(values <= 0)):
            raise ValueError("route_costs must contain one positive finite number per route column")
        costs = values.astype(np.float64)
    sensor_constraint = LinearConstraint(matrix, counts, counts)
    if prior is None:
        objective = costs
        constraints = [sensor_constraint]
        variable_count = route_count
        objective_name = "minimum_vehicle_count" if route_costs is None else "minimum_route_cost"
    else:
        prior_integer = _exact_nonnegative_integer_array(prior, "prior")
        if prior_integer.ndim != 1 or prior_integer.shape[0] != route_count:
            raise ValueError("prior must have one value per route column")
        prior_counts = prior_integer.astype(np.float64)
        # z = (x, positive deviation, negative deviation), with
        # x - positive + negative = prior.
        objective = np.concatenate(
            [np.zeros(route_count), costs, costs]
        )
        sensor_constraint = LinearConstraint(
            np.column_stack([matrix, np.zeros((matrix.shape[0], route_count * 2))]),
            counts,
            counts,
        )
        prior_constraint = LinearConstraint(
            np.column_stack([np.eye(route_count), -np.eye(route_count), np.eye(route_count)]),
            prior_counts,
            prior_counts,
        )
        constraints = [sensor_constraint, prior_constraint]
        variable_count = route_count * 3
        integrality = np.concatenate(
            [np.ones(route_count), np.zeros(route_count * 2)]
        )
        objective_name = "minimum_l1_change_from_prior" if route_costs is None else "minimum_weighted_l1_change_from_prior"

    if prior is None:
        integrality = np.ones(variable_count)

    result = milp(
        c=objective,
        integrality=integrality,
        bounds=Bounds(np.zeros(variable_count), np.full(variable_count, np.inf)),
        constraints=constraints,
        options={"time_limit": time_limit_seconds},
    )
    if not result.success or result.x is None:
        failure_kinds = {
            1: "limit_reached",
            2: "infeasible",
            3: "unbounded",
            4: "solver_failure",
        }
        return {
            "status": "blocked",
            "objective": objective_name,
            "failure_kind": failure_kinds.get(int(result.status), "solver_failure"),
            "solver_status": int(result.status),
            "solver_message": str(result.message),
            "solution": None,
            **({"route_costs": costs.tolist()} if route_costs is not None else {}),
        }

    solution = np.rint(result.x[:route_count]).astype(np.int64)
    measured = matrix.astype(np.int64) @ solution
    expected = counts_integer
    residual = measured - expected
    if np.any(residual != 0):
        raise RuntimeError("integer solver returned a non-exact sensor solution")
    return {
        "status": "pass",
        "objective": objective_name,
        "objective_value": float(result.fun),
        "solver_status": int(result.status),
        "solver_message": str(result.message),
        "solution": solution.tolist(),
        "vehicle_count": int(solution.sum()),
        "active_route_count": int(np.count_nonzero(solution)),
        "predicted_sensor_counts": measured.tolist(),
        "residual": residual.tolist(),
        "rank": int(np.linalg.matrix_rank(matrix)),
        "nullity": int(route_count - np.linalg.matrix_rank(matrix)),
        **({"route_costs": costs.tolist()} if route_costs is not None else {}),
    }


def _exact_nonnegative_integer_array(value: object, name: str) -> np.ndarray:
    array = np.asarray(value)
    if not np.issubdtype(array.dtype, np.number) or np.issubdtype(array.dtype, np.complexfloating):
        raise ValueError(f"{name} must contain finite non-negative integers")
    if np.any(~np.isfinite(array)) or np.any(array < 0) or np.any(array != np.rint(array)):
        raise ValueError(f"{name} must contain finite non-negative integers")
    # HiGHS receives float64 coefficients. Reject integers that cannot be
    # represented exactly instead of silently solving a different system.
    if np.any(array > 2**53):
        raise ValueError(f"{name} contains an integer outside the exact float64 range")
    return array.astype(np.int64)


def write_prototype_flow_demand(*, source_routes: Path, source_tripinfo: Path, passages: list[dict],
                               vehicle_counts: np.ndarray, comparison_begin_s: float,
                               bin_seconds: float, step_length: float, output_file: Path) -> dict:
    """Place each prototype's measured passages inside its requested count bin.

    Columns follow vehicle order in the source route file. Realized boundary
    lanes and speed factors remain fixed. Changed traffic still requires replay.
    """
    from .candidate_contracts import file_sha256

    if (not all(math.isfinite(value) for value in (comparison_begin_s, bin_seconds, step_length))
            or comparison_begin_s < 0 or bin_seconds <= 0 or step_length <= 0 or bin_seconds <= 2 * step_length):
        raise ValueError("Use finite nonnegative comparison time and positive bin and simulation steps.")
    source_routes, source_tripinfo = Path(source_routes).resolve(strict=True), Path(source_tripinfo).resolve(strict=True)
    output_file = Path(output_file).resolve()
    if output_file.exists() or output_file in {source_routes, source_tripinfo}:
        raise ValueError("Write prototype demand to a new file separate from its sources.")
    inputs = [{"path": str(path), "sha256": file_sha256(path)} for path in (source_routes, source_tripinfo)]
    counts = _exact_nonnegative_integer_array(vehicle_counts, "vehicle_counts")
    root = ET.parse(source_routes).getroot()
    if root.tag != "routes" or any(row.tag not in {"vehicle", "vType", "vTypeDistribution", "route", "routeDistribution"} for row in root):
        raise ValueError("Prototype demand must contain vehicle templates and type or route definitions only.")
    templates = root.findall("vehicle")
    identities = [vehicle.get("id") for vehicle in templates]
    if not identities or any(not value for value in identities) or len(set(identities)) != len(identities):
        raise ValueError("Prototype vehicles must have unique IDs.")
    if counts.ndim != 2 or counts.shape[1] != len(templates):
        raise ValueError("Count columns must match source prototype vehicles.")
    trip_root = ET.parse(source_tripinfo).getroot()
    trip_rows = trip_root.findall("tripinfo")
    trips = {row.get("id"): row for row in trip_rows}
    if trip_root.tag != "tripinfos" or len(trips) != len(trip_rows):
        raise ValueError("Prototype trip information must have unique trip IDs.")
    delays = defaultdict(list)
    source_departures = {vehicle.get("id"): float(vehicle.get("depart", "nan")) for vehicle in templates}
    for row in passages:
        vehicle = row["vehicle_id"]
        if vehicle not in source_departures:
            raise ValueError("A passage vehicle is absent from the prototype demand.")
        delay = float(row["notification_time_s"]) - source_departures[vehicle]
        if not math.isfinite(delay) or delay < 0:
            raise ValueError("Prototype passage delays must be finite and nonnegative.")
        delays[vehicle].append(delay)
    result = ET.Element(root.tag, root.attrib)
    result.extend(copy.deepcopy(row) for row in root if row.tag != "vehicle")
    generated, assignments = [], []
    for column, template in enumerate(templates):
        if not np.any(counts[:, column]):
            continue
        identity = template.get("id")
        trip = trips.get(identity)
        route = template.find("route")
        if (trip is None or not math.isfinite(float(trip.get("arrival", "nan"))) or float(trip.get("arrival", "-1")) < 0
                or trip.get("vaporized", "") or route is None or len(template.findall("route")) != 1
                or not route.get("edges") or any(row.tag not in {"route", "param"} for row in template)):
            raise ValueError("Used prototypes require completed trips and inline routes without intermediate stops.")
        if not delays[identity]:
            raise ValueError("A used count prototype has no measured field passage.")
        first, last = min(delays[identity]), max(delays[identity])
        available = bin_seconds - 2 * step_length - (last - first)
        if available <= 0:
            raise ValueError("The prototype spans more than one count bin. Use a time-expanded response model.")
        lane = trip.get("departLane", "").rsplit("_", 1)
        if len(lane) != 2 or lane[0] != route.get("edges").split()[0] or not lane[1].isdigit():
            raise ValueError("The realized prototype departure lane does not match its first road.")
        factor = trip.get("speedFactor")
        if factor is not None and (not math.isfinite(float(factor)) or float(factor) <= 0):
            raise ValueError("Realized prototype speed factors must be positive and finite.")
        for bin_index, count in enumerate(counts[:, column]):
            for within in range(int(count)):
                departure = (comparison_begin_s + bin_index * bin_seconds + step_length - first
                             + (within + .5) * available / count)
                if not math.isfinite(departure) or departure < 0:
                    raise ValueError("The requested first bin needs an earlier initialization period.")
                vehicle = copy.deepcopy(template)
                vehicle_id = f"fit_b{bin_index}_p{column}_v{within}"
                vehicle.set("id", vehicle_id)
                vehicle.set("depart", f"{departure:.12f}")
                vehicle.set("departLane", lane[1])
                if trip.get("speedFactor") is not None:
                    vehicle.set("speedFactor", trip.get("speedFactor"))
                generated.append(vehicle)
                assignments.append({"vehicle_id": vehicle_id, "prototype_id": identity,
                                    "target_bin": bin_index, "planned_depart_s": departure})
    result.extend(sorted(generated, key=lambda row: (float(row.get("depart")), row.get("id"))))
    output_file.parent.mkdir(parents=True, exist_ok=True)
    with output_file.open("xb") as stream:
        stream.write(ET.tostring(result, encoding="utf-8", xml_declaration=True))
    return {"status": "proposal", "inputs": inputs,
        "inputs_unchanged": all(file_sha256(Path(row["path"])) == row["sha256"] for row in inputs),
        "output": {"path": str(output_file), "sha256": file_sha256(output_file)}, "vehicle_count": len(generated),
        "comparison_begin_s": comparison_begin_s, "bin_seconds": bin_seconds,
        "assignments": assignments, "claim_boundary": "Prototype-based departure proposal only. Native replay must measure every field and bin again."}


def audit_route_sensor_incidence(
    candidate_manifest_csv: Path,
    edge_data_file: Path,
) -> dict[str, object]:
    """Describe what edge counts can identify about candidate-route flows.

    For one measurement interval, ``A[s, r]`` is the number of times route
    ``r`` traverses measured edge ``s``.  A count vector therefore constrains
    route multiplicities through ``A @ x = y``.  This audit intentionally does
    not solve that system.  It records rank, nullity and indistinguishable
    route classes before a sampler or optimizer chooses one feasible member.

    The matrix is static: it does not model travel-time shifts between bins,
    lane changes or congestion.  SUMO detector output remains the authoritative
    outer-loop validation for those effects.
    """

    candidate_path = Path(candidate_manifest_csv).resolve(strict=True)
    edge_data_path = Path(edge_data_file).resolve(strict=True)

    with candidate_path.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    route_ids: list[str] = []
    route_edges: list[tuple[str, ...]] = []
    for row in rows:
        route_id = str(row.get("route_id", "")).strip()
        edges = tuple(str(row.get("edges", "")).split())
        if not route_id or not edges:
            continue
        if route_id in route_ids:
            raise ValueError(f"duplicate candidate route id: {route_id}")
        route_ids.append(route_id)
        route_edges.append(edges)
    if not route_ids:
        raise ValueError("candidate route manifest contains no usable routes")

    root = ET.parse(edge_data_path).getroot()
    intervals = root.findall("interval")
    constrained_edges = sorted(
        {
            str(edge.attrib.get("id", "")).strip()
            for interval in intervals
            for edge in interval.findall("edge")
            if str(edge.attrib.get("id", "")).strip()
        }
    )
    if not constrained_edges:
        raise ValueError("edge count file contains no constrained edges")

    matrix = np.asarray(
        [
            [edges.count(edge_id) for edges in route_edges]
            for edge_id in constrained_edges
        ],
        dtype=np.int64,
    )
    rank = int(np.linalg.matrix_rank(matrix))
    sensor_overlap = matrix @ matrix.T
    singular_values = [float(value) for value in np.linalg.svd(matrix, compute_uv=False)]
    nonzero_singular_values = [value for value in singular_values if value > 1e-12]
    route_count = len(route_ids)
    interval_count = len(intervals)

    classes: dict[tuple[int, ...], list[str]] = defaultdict(list)
    for column, route_id in enumerate(route_ids):
        signature = tuple(int(value) for value in matrix[:, column])
        classes[signature].append(route_id)
    class_rows = [
        {
            "signature": list(signature),
            "route_count": len(members),
            "route_ids": sorted(members),
        }
        for signature, members in sorted(classes.items())
    ]
    zero_signature = tuple(0 for _ in constrained_edges)
    zero_routes = sorted(classes.get(zero_signature, []))
    equivalent_classes = [row for row in class_rows if int(row["route_count"]) > 1]

    identifiability_status = (
        "underdetermined-unbounded-without-prior"
        if zero_routes
        else "underdetermined"
        if rank < route_count
        else "full-column-rank"
    )
    return {
        "status": "pass",
        "claim_status": identifiability_status,
        "identifiability_status": identifiability_status,
        "matrix_definition": "A[s,r] = traversals of measured edge s by candidate route r",
        "shape": {"sensor_rows": len(constrained_edges), "route_columns": route_count},
        "constrained_edges": constrained_edges,
        "route_ids": route_ids,
        "rank": rank,
        "nullity": route_count - rank,
        "singular_values": singular_values,
        "observed_subspace_condition_number": (
            max(nonzero_singular_values) / min(nonzero_singular_values)
            if nonzero_singular_values
            else None
        ),
        "sensor_route_overlap": {
            "edge_order": constrained_edges,
            "matrix": sensor_overlap.tolist(),
            "definition": "(A @ A.T)[i,j] counts candidate-route traversals shared by measured edges i and j",
        },
        "interval_count": interval_count,
        "independent_bin_expansion": {
            "variable_count": route_count * interval_count,
            "rank_upper_bound": rank * interval_count,
            "nullity_lower_bound": (route_count - rank) * interval_count,
        },
        "distinct_route_signature_count": len(classes),
        "equivalent_route_class_count": len(equivalent_classes),
        "equivalent_route_classes": equivalent_classes,
        "unobserved_route_count": len(zero_routes),
        "unobserved_route_ids": zero_routes,
        "all_route_signature_classes": class_rows,
        "claim_boundary": (
            "Static edge-incidence identifiability only. Equal columns cannot be distinguished by these counts. "
            "A zero-incidence route is unconstrained and may make the feasible set unbounded unless a boundary or "
            "prior constraint is supplied. Travel-time shifts, lane selection, queues and signal control must be "
            "evaluated from SUMO E1 output."
        ),
    }
