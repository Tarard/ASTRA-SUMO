from __future__ import annotations

import csv
import math
import xml.etree.ElementTree as ET
from collections import defaultdict
from pathlib import Path

import numpy as np
from scipy.optimize import Bounds, LinearConstraint, milp


def solve_nonnegative_integer_route_flows(
    response_matrix: np.ndarray,
    observations: np.ndarray,
    *,
    prior: np.ndarray | None = None,
    time_limit_seconds: float = 60.0,
) -> dict[str, object]:
    """Find one exact integer route-flow vector for a sensor response matrix.

    ``response_matrix[s, r]`` is the number of contributions made by one
    vehicle on route ``r`` to sensor group ``s``.  When a prior is supplied,
    the solver minimizes the L1 change from that prior; otherwise it minimizes
    the number of vehicles.  The function deliberately returns ``blocked``
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
    sensor_constraint = LinearConstraint(matrix, counts, counts)
    if prior is None:
        objective = np.ones(route_count, dtype=np.float64)
        constraints = [sensor_constraint]
        variable_count = route_count
        objective_name = "minimum_vehicle_count"
    else:
        prior_integer = _exact_nonnegative_integer_array(prior, "prior")
        if prior_integer.ndim != 1 or prior_integer.shape[0] != route_count:
            raise ValueError("prior must have one value per route column")
        prior_counts = prior_integer.astype(np.float64)
        # z = (x, positive deviation, negative deviation), with
        # x - positive + negative = prior.
        objective = np.concatenate(
            [np.zeros(route_count), np.ones(route_count * 2)]
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
        objective_name = "minimum_l1_change_from_prior"

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
