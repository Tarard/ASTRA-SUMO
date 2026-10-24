"""Bounded OD proposals from observed assignment, subject to native remeasurement."""

import math

import numpy as np
from scipy.optimize import Bounds, LinearConstraint, milp
from scipy.sparse import csr_matrix, eye, hstack

from .route_sensor_matrix import _exact_nonnegative_integer_array


def propose_bounded_od_assignment(*, contributions, prior, observations, od_groups,
                                  lower, upper, exact_rows, change_budget,
                                  time_limit_seconds=60.0):
    """Minimize predicted L1 error, then OD change, keeping each OD total fixed.

    Columns describe observed contributions of all vehicles in one OD period.
    Dividing by the positive prior flow gives an assignment fraction, not a
    derivative. Bounds can freeze initialization periods. Exact rows constrain
    only the proposal; every native observation must be measured again.
    """
    counts = _exact_nonnegative_integer_array(contributions, "contributions")
    x0 = _exact_nonnegative_integer_array(prior, "prior")
    target = _exact_nonnegative_integer_array(observations, "observations")
    lo = _exact_nonnegative_integer_array(lower, "lower")
    hi = _exact_nonnegative_integer_array(upper, "upper")
    groups = _exact_nonnegative_integer_array(od_groups, "od_groups")
    exact = np.asarray(exact_rows)
    if (counts.ndim != 2 or not all(counts.shape) or x0.ndim != 1
            or counts.shape[1] != len(x0) or np.any(x0 == 0)):
        raise ValueError("Use a nonempty contribution matrix and one positive observed prior per column.")
    m, n = counts.shape
    if (target.shape != (m,) or exact.shape != (m,) or exact.dtype != np.dtype(bool)
            or any(v.shape != (n,) for v in (lo, hi, groups))):
        raise ValueError("Targets and Boolean exact rows must match rows; bounds and OD groups must match columns.")
    if np.any(lo > x0) or np.any(hi < x0):
        raise ValueError("Cell bounds must contain the observed prior.")
    if (isinstance(change_budget, bool) or not isinstance(change_budget, int)
            or change_budget < 0 or change_budget > 2**53):
        raise ValueError("Use a nonnegative integer total change budget within the exact float64 range.")
    if not math.isfinite(time_limit_seconds) or time_limit_seconds <= 0:
        raise ValueError("Use a finite positive solver time limit.")
    if sum(map(int, x0)) > 2**53:
        raise ValueError("The total prior must remain within the exact float64 integer range.")
    matrix = counts.astype(float) / x0
    a, identity = csr_matrix(matrix), eye(n, format="csr")
    zero_nm, zero_mn = csr_matrix((n, m)), csr_matrix((m, n))
    row_identity = eye(m, format="csr")
    group_labels = np.unique(groups)
    group_matrix = csr_matrix(np.array([groups == g for g in group_labels], dtype=float))
    group_totals = [sum(map(int, x0[groups == g])) for g in group_labels]
    # Variables are integer OD flows, continuous absolute flow changes, and count errors.
    constraints = [
        LinearConstraint(hstack([a, zero_mn, -row_identity]), -np.inf, target),
        LinearConstraint(hstack([a, zero_mn, row_identity]), target, np.inf),
        LinearConstraint(hstack([identity, -identity, zero_nm]), -np.inf, x0),
        LinearConstraint(hstack([identity, identity, zero_nm]), x0, np.inf),
        LinearConstraint(hstack([group_matrix, csr_matrix((len(group_totals), n + m))]),
                         group_totals, group_totals),
        LinearConstraint(csr_matrix(np.r_[np.zeros(n), np.ones(n), np.zeros(m)][None, :]),
                         0, change_budget),
    ]
    if np.any(exact):
        constraints.append(LinearConstraint(hstack([a[exact], csr_matrix((int(exact.sum()), n + m))]),
                                            target[exact], target[exact]))
    bounds = Bounds(np.r_[lo, np.zeros(n + m)], np.r_[hi, np.full(n + m, np.inf)])
    integer = np.r_[np.ones(n), np.zeros(n + m)]
    objective = np.r_[np.zeros(2 * n), np.ones(m)]
    options = {"time_limit": time_limit_seconds, "mip_rel_gap": 0.0}
    fit = milp(objective, integrality=integer, bounds=bounds, constraints=constraints, options=options)
    boundary = "Observed assignment is not a Jacobian. Predicted fit is not native count fit, field OD, or calibrated control."
    if not fit.success or fit.x is None:
        return {"status": "blocked", "stage": "predicted_fit", "solver_message": str(fit.message),
                "solution": None, "formal_result": False, "claim_boundary": boundary}
    tolerance = 1e-6
    constraints.append(LinearConstraint(csr_matrix(objective[None, :]), 0, fit.fun + tolerance))
    nearest = milp(np.r_[np.zeros(n), np.ones(n), np.zeros(m)], integrality=integer,
                   bounds=bounds, constraints=constraints, options=options)
    if not nearest.success or nearest.x is None:
        return {"status": "blocked", "stage": "minimum_change", "solver_message": str(nearest.message),
                "solution": None, "formal_result": False, "claim_boundary": boundary}
    solution = np.rint(nearest.x[:n]).astype(np.int64)
    predicted = matrix @ solution
    residual = predicted - target
    changes = solution - x0
    if (np.max(np.abs(nearest.x[:n] - solution)) > tolerance
            or np.any(solution < lo) or np.any(solution > hi)
            or int(np.abs(changes).sum()) > change_budget
            or [sum(map(int, solution[groups == g])) for g in group_labels] != group_totals
            or np.any(np.abs(residual[exact]) > tolerance)
            or np.abs(residual).sum() > fit.fun + 2 * tolerance):
        raise RuntimeError("The integer OD proposal violates its declared constraints.")
    return {"status": "candidate", "solution": solution.tolist(), "changes": changes.tolist(),
            "changed_cells": int(np.count_nonzero(changes)), "l1_change": int(np.abs(changes).sum()),
            "change_budget": change_budget, "vehicle_count": int(solution.sum()),
            "predicted_counts": predicted.tolist(), "predicted_residuals": residual.tolist(),
            "predicted_TAE": float(np.abs(residual).sum()), "predicted_MAE": float(np.abs(residual).mean()),
            "prior_TAE": int(np.abs(counts.sum(axis=1) - target).sum()),
            "od_group_totals": group_totals, "exact_rows": int(exact.sum()),
            "zero_observed_columns": np.flatnonzero(counts.sum(axis=0) == 0).tolist(),
            "objective": "minimum predicted L1 error, then minimum OD flow change within 1e-6 fit tolerance",
            "solver_messages": [str(fit.message), str(nearest.message)],
            "formal_result": False, "claim_boundary": boundary}
